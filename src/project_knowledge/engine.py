from __future__ import annotations

import ast
import fnmatch
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import ProjectConfig
from .models import ParseResult, Relation, Route, Symbol
from .util import hash_text, read_text


LANGUAGES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".cs": "C#",
    ".lua": "Lua",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".hpp": "C/C++",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
}

TEXT_CONFIG_EXTENSIONS = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".xml",
    ".sql",
    ".graphql",
    ".proto",
}


@dataclass(slots=True)
class IndexedFile:
    path: str
    language: str
    module: str
    size: int
    mtime_ns: int
    content_hash: str


class CodeIndexEngine(ABC):
    @abstractmethod
    def discover(self, root: Path, config: ProjectConfig) -> list[IndexedFile]:
        raise NotImplementedError

    @abstractmethod
    def parse(self, root: Path, indexed_file: IndexedFile) -> ParseResult:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> dict[str, object]:
        raise NotImplementedError


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(
        pattern in {"*", "**", "**/*"}
        or fnmatch.fnmatch(path, pattern)
        or fnmatch.fnmatch("/" + path, pattern)
        for pattern in patterns
    )


def _module_for(path: str) -> str:
    parts = Path(path).parts
    if not parts:
        return "root"
    if parts[0] in {"src", "lib", "app", "packages", "services", "cmd"} and len(parts) > 1:
        return parts[1]
    return parts[0] if len(parts) > 1 else "root"


class BuiltinCodeIndexEngine(CodeIndexEngine):
    """Offline parser with precise Python AST support and conservative fallbacks."""

    def discover(self, root: Path, config: ProjectConfig) -> list[IndexedFile]:
        files: list[IndexedFile] = []
        for directory, child_directories, names in os.walk(root, topdown=True, followlinks=False):
            base = Path(directory)
            relative_base = base.relative_to(root).as_posix()
            child_directories[:] = [
                name for name in child_directories
                if not _matches(
                    f"{relative_base}/{name}/placeholder" if relative_base != "." else f"{name}/placeholder",
                    config.exclude,
                )
            ]
            for name in names:
                candidate = base / name
                if candidate.is_symlink():
                    continue
                relative = candidate.relative_to(root).as_posix()
                if _matches(relative, config.exclude) or not _matches(relative, config.include):
                    continue
                language = LANGUAGES.get(candidate.suffix.lower())
                if language is None and candidate.suffix.lower() not in TEXT_CONFIG_EXTENSIONS:
                    continue
                try:
                    data = candidate.read_bytes()
                    stat = candidate.stat()
                except OSError:
                    continue
                if len(data) > 2_000_000 or b"\x00" in data[:8192]:
                    continue
                files.append(
                    IndexedFile(
                        path=relative,
                        language=language or "Configuration",
                        module=_module_for(relative),
                        size=len(data),
                        mtime_ns=stat.st_mtime_ns,
                        content_hash="sha256:" + __import__("hashlib").sha256(data).hexdigest(),
                    )
                )
        return sorted(files, key=lambda item: item.path)

    def parse(self, root: Path, indexed_file: IndexedFile) -> ParseResult:
        text = read_text(root / indexed_file.path)
        if indexed_file.language == "Python":
            return PythonParser(indexed_file.path, text).parse()
        if indexed_file.language == "Configuration":
            return ParseResult(parser="config")
        return GenericParser(indexed_file.path, text, indexed_file.language).parse()

    def status(self) -> dict[str, object]:
        return {
            "engine": "builtin",
            "capabilities": ["symbols", "imports", "calls", "inheritance", "routes"],
            "precise_languages": ["Python"],
            "conservative_languages": sorted(set(LANGUAGES.values()) - {"Python"}),
            "limitations": ["dynamic dispatch", "reflection", "runtime dependency injection"],
        }


class PythonParser(ast.NodeVisitor):
    ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "route"}

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text
        self.lines = text.splitlines()
        self.result = ParseResult(parser="python-ast")
        self.scope: list[str] = []
        self.scope_ids: list[str] = []

    def parse(self) -> ParseResult:
        try:
            tree = ast.parse(self.text, filename=self.path)
        except SyntaxError as error:
            self.result.parse_error = f"{error.msg} (line {error.lineno})"
            return self.result
        module_id = f"{self.path}::<module>"
        self.result.symbols.append(
            Symbol(module_id, Path(self.path).stem, "module", self.path, 1, len(self.lines), source_hash=hash_text(self.text))
        )
        self.scope_ids.append(module_id)
        self.visit(tree)
        self.scope_ids.pop()
        return self.result

    def _qualname(self, name: str) -> str:
        return ".".join([*self.scope, name])

    def _source_hash(self, node: ast.AST) -> str:
        segment = ast.get_source_segment(self.text, node) or ""
        return hash_text(segment)

    def _signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = [argument.arg for argument in [*node.args.posonlyargs, *node.args.args]]
        if node.args.vararg:
            args.append("*" + node.args.vararg.arg)
        args.extend(argument.arg for argument in node.args.kwonlyargs)
        if node.args.kwarg:
            args.append("**" + node.args.kwarg.arg)
        return f"{node.name}({', '.join(args)})"

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = self._qualname(node.name)
        symbol_id = f"{self.path}::{qualname}"
        kind = "method" if self.scope else "function"
        self.result.symbols.append(
            Symbol(symbol_id, node.name, kind, self.path, node.lineno, getattr(node, "end_lineno", None), self._signature(node), self._source_hash(node))
        )
        self._routes(node, symbol_id)
        self.scope.append(node.name)
        self.scope_ids.append(symbol_id)
        self.generic_visit(node)
        self.scope_ids.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._qualname(node.name)
        symbol_id = f"{self.path}::{qualname}"
        self.result.symbols.append(
            Symbol(symbol_id, node.name, "class", self.path, node.lineno, getattr(node, "end_lineno", None), source_hash=self._source_hash(node))
        )
        for base in node.bases:
            self.result.relations.append(Relation(symbol_id, self._name(base), "inherits", self.path, node.lineno, 0.95))
        self.scope.append(node.name)
        self.scope_ids.append(symbol_id)
        self.generic_visit(node)
        self.scope_ids.pop()
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        source = self.scope_ids[-1]
        for alias in node.names:
            self.result.relations.append(Relation(source, alias.name, "imports", self.path, node.lineno, 1.0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        source = self.scope_ids[-1]
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            self.result.relations.append(Relation(source, f"{module}.{alias.name}".strip("."), "imports", self.path, node.lineno, 1.0))

    def visit_Call(self, node: ast.Call) -> None:
        target = self._name(node.func)
        if target:
            self.result.relations.append(Relation(self.scope_ids[-1], target, "calls", self.path, getattr(node, "lineno", None), 0.8))
        self.generic_visit(node)

    def _routes(self, node: ast.FunctionDef | ast.AsyncFunctionDef, handler: str) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            dotted = self._name(decorator.func)
            method = dotted.rsplit(".", 1)[-1].lower()
            if method not in self.ROUTE_METHODS or not decorator.args:
                continue
            route = self._literal(decorator.args[0])
            if route is None:
                continue
            if method == "route":
                methods = next((keyword.value for keyword in decorator.keywords if keyword.arg == "methods"), None)
                route_methods = [self._literal(item) for item in methods.elts] if isinstance(methods, (ast.List, ast.Tuple)) else ["ANY"]
            else:
                route_methods = [method.upper()]
            for route_method in route_methods:
                self.result.routes.append(Route(route_method or "ANY", route, handler, self.path, node.lineno))

    @staticmethod
    def _name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = PythonParser._name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        if isinstance(node, ast.Subscript):
            return PythonParser._name(node.value)
        return ""

    @staticmethod
    def _literal(node: ast.AST) -> str | None:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class GenericParser:
    DEFINITION_PATTERNS = [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")),
        ("interface", re.compile(r"^\s*(?:export\s+)?(?:public\s+)?(?:interface|trait)\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|fn|func|def)\s+([A-Za-z_$][\w$]*)\s*\(")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>")),
        ("function", re.compile(r"^\s*(?:local\s+)?function\s+([A-Za-z_$][\w$.:]*)\s*\(")),
        ("type", re.compile(r"^\s*(?:export\s+)?(?:type|struct|enum|record)\s+([A-Za-z_$][\w$]*)")),
    ]
    IMPORT_PATTERNS = [
        re.compile(r"\bfrom\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"),
        re.compile(r"^\s*(?:use|using|import)\s+([^;]+)"),
    ]
    ROUTE_PATTERN = re.compile(r"\.(get|post|put|patch|delete|options|head)\s*\(\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)

    def __init__(self, path: str, text: str, language: str):
        self.path = path
        self.text = text
        self.language = language

    def parse(self) -> ParseResult:
        result = ParseResult(parser=f"generic-{self.language.lower()}")
        module_id = f"{self.path}::<module>"
        lines = self.text.splitlines()
        result.symbols.append(Symbol(module_id, Path(self.path).stem, "module", self.path, 1, len(lines), source_hash=hash_text(self.text), confidence=1.0))
        current = module_id
        for number, line in enumerate(lines, 1):
            for kind, pattern in self.DEFINITION_PATTERNS:
                match = pattern.search(line)
                if match:
                    name = match.group(1)
                    current = f"{self.path}::{name}"
                    result.symbols.append(Symbol(current, name, kind, self.path, number, signature=line.strip()[:240], source_hash=hash_text(line), confidence=0.72))
                    break
            for pattern in self.IMPORT_PATTERNS:
                match = pattern.search(line)
                if match:
                    result.relations.append(Relation(module_id, match.group(1).strip(), "imports", self.path, number, 0.75))
                    break
            for match in self.ROUTE_PATTERN.finditer(line):
                result.routes.append(Route(match.group(1).upper(), match.group(2), current, self.path, number))
            for call in re.finditer(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(", line):
                target = call.group(1)
                if target not in {"if", "for", "while", "switch", "catch", "function", "return"}:
                    result.relations.append(Relation(current, target, "calls", self.path, number, 0.45))
        return result


def create_engine(config: ProjectConfig) -> CodeIndexEngine:
    if config.engine not in {"builtin", "codegraph"}:
        raise ValueError(f"unsupported index engine: {config.engine}")
    # The public adapter boundary is stable; builtin remains the offline fallback when
    # an external CodeGraph executable is not configured.
    return BuiltinCodeIndexEngine()
