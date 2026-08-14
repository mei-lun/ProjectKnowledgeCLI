from __future__ import annotations

import ast
import fnmatch
import os
import re
from collections import deque
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
    ".conf",
    ".properties",
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

    def diagnose(self, root: Path) -> dict[str, object]:
        """Return side-effect-free runtime availability for this project."""
        return self.status()

    def initialize(self, root: Path, config: ProjectConfig) -> dict[str, object]:
        raise NotImplementedError

    def sync(self, root: Path, config: ProjectConfig, previous: dict[str, str] | None = None) -> dict[str, object]:
        raise NotImplementedError

    def search_symbols(self, root: Path, config: ProjectConfig, query: str, limit: int = 20) -> list[Symbol]:
        raise NotImplementedError

    def get_source(self, root: Path, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        raise NotImplementedError

    def trace(self, root: Path, symbol_id: str, config: ProjectConfig, max_depth: int = 1, limit: int = 200) -> list[Relation]:
        raise NotImplementedError

    def impact(self, root: Path, config: ProjectConfig, files: list[str] | None = None, symbols: list[str] | None = None, max_hops: int = 1, max_relations: int = 500) -> dict[str, object]:
        raise NotImplementedError

    def affected_tests(self, root: Path, config: ProjectConfig, files: list[str]) -> list[str]:
        raise NotImplementedError

    def entrypoints(self, root: Path, config: ProjectConfig, limit: int = 200) -> list[dict[str, object]]:
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
    if parts[0] in {"dev", "skynet", "service", "config"} and len(parts) > 1:
        return f"{parts[0]}/{parts[1]}"
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
        if indexed_file.language == "Lua":
            return LuaParser(indexed_file.path, text).parse()
        if indexed_file.path.lower().endswith(".sql"):
            return SQLParser(indexed_file.path, text).parse()
        if indexed_file.language == "Configuration":
            return ConfigParser(indexed_file.path, text).parse()
        return GenericParser(indexed_file.path, text, indexed_file.language).parse()

    def _parse_workspace(self, root: Path, config: ProjectConfig) -> list[tuple[IndexedFile, ParseResult]]:
        parsed: list[tuple[IndexedFile, ParseResult]] = []
        for indexed_file in self.discover(root, config):
            parsed.append((indexed_file, self.parse(root, indexed_file)))
        return parsed

    def initialize(self, root: Path, config: ProjectConfig) -> dict[str, object]:
        discovered = self.discover(root, config)
        parsed = [(item, self.parse(root, item)) for item in discovered]
        relations = sum(len(result.relations) for _, result in parsed)
        return {
            "files": len(discovered),
            "symbols": sum(len(result.symbols) for _, result in parsed),
            "relations": relations,
            "modules": len({item.module for item in discovered}),
            "parse_errors": sum(1 for _, result in parsed if result.parse_error),
            "parse_success_rate": round(sum(1 for _, result in parsed if not result.parse_error) / len(discovered), 4) if discovered else 1.0,
        }

    def sync(self, root: Path, config: ProjectConfig, previous: dict[str, str] | None = None) -> dict[str, object]:
        discovered = self.discover(root, config)
        current = {item.path: item.content_hash for item in discovered}
        previous = previous or {}
        summary = self.initialize(root, config)
        summary["changed_files"] = sorted(path for path in set(current) | set(previous) if current.get(path) != previous.get(path))
        summary["content_hashes"] = current
        return summary

    def search_symbols(self, root: Path, config: ProjectConfig, query: str, limit: int = 20) -> list[Symbol]:
        query_lower = query.lower()
        normalized_query = query_lower.replace(".", ":")
        candidates: list[tuple[int, Symbol]] = []
        for _, result in self._parse_workspace(root, config):
            for symbol in result.symbols:
                identifier = symbol.id.lower()
                name = symbol.name.lower()
                rank = 0 if name == query_lower or name == normalized_query or identifier.endswith("::" + query_lower) or identifier.endswith("::" + normalized_query) else 1 if query_lower in name or normalized_query in name or query_lower in identifier or normalized_query in identifier else 2
                if rank < 2:
                    candidates.append((rank, symbol))
        candidates.sort(key=lambda item: (item[0], -item[1].confidence, len(item[1].name), item[1].id))
        return [symbol for _, symbol in candidates[:max(1, min(limit, 200))]]

    def get_source(self, root: Path, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("source path must remain inside project")
        target = (root / relative).resolve()
        if root.resolve() not in target.parents and target != root.resolve():
            raise ValueError("source path must remain inside project")
        lines = read_text(target).splitlines()
        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))
        if start > end:
            return ""
        return "\n".join(lines[start - 1:end])

    def trace(self, root: Path, symbol_id: str, config: ProjectConfig, max_depth: int = 1, limit: int = 200) -> list[Relation]:
        relations = [relation for _, result in self._parse_workspace(root, config) for relation in result.relations]
        frontier = {symbol_id}
        selected: list[Relation] = []
        seen: set[tuple[str, str, str, str, int | None]] = set()
        for _ in range(max(0, min(max_depth, 5))):
            if not frontier or len(selected) >= limit:
                break
            next_frontier: set[str] = set()
            for relation in relations:
                key = (relation.source, relation.target, relation.kind, relation.path, relation.line)
                if key in seen or (relation.source not in frontier and relation.target not in frontier):
                    continue
                seen.add(key)
                selected.append(relation)
                if relation.source in frontier:
                    next_frontier.add(relation.target)
                if relation.target in frontier:
                    next_frontier.add(relation.source)
                if len(selected) >= limit:
                    break
            frontier = next_frontier
        return selected

    def impact(self, root: Path, config: ProjectConfig, files: list[str] | None = None, symbols: list[str] | None = None, max_hops: int = 1, max_relations: int = 500) -> dict[str, object]:
        files = [Path(path).as_posix().lstrip("./") for path in (files or [])]
        symbols = list(symbols or [])
        parsed = self._parse_workspace(root, config)
        all_symbols = {symbol.id: (item, symbol) for item, result in parsed for symbol in result.symbols}
        anchors = set(symbols)
        anchors.update(symbol.id for item, symbol in all_symbols.values() if item.path in files)
        relations = [relation for _, result in parsed for relation in result.relations]
        expanded = set(anchors)
        selected: list[Relation] = []
        frontier = set(anchors)
        seen: set[tuple[str, str, str, str, int | None]] = set()
        relation_hops: dict[str, int] = {}
        for hop in range(1, max(0, min(max_hops, 5)) + 1):
            if not frontier or len(selected) >= max_relations:
                break
            next_frontier: set[str] = set()
            hop_count = 0
            for relation in relations:
                key = (relation.source, relation.target, relation.kind, relation.path, relation.line)
                if key in seen or (relation.source not in frontier and relation.target not in frontier):
                    continue
                seen.add(key)
                relation.resolved = relation.target in all_symbols
                selected.append(relation)
                hop_count += 1
                for endpoint in (relation.source, relation.target):
                    if endpoint not in expanded:
                        next_frontier.add(endpoint)
                    expanded.add(endpoint)
                if len(selected) >= max_relations:
                    break
            if hop_count:
                relation_hops[str(hop)] = hop_count
            frontier = next_frontier
        affected_files = set(files)
        affected_files.update(all_symbols[symbol_id][0].path for symbol_id in expanded if symbol_id in all_symbols)
        modules = sorted({item.module for item, _ in parsed if item.path in affected_files})
        return {
            "affected_files": sorted(affected_files),
            "affected_symbols": sorted(expanded),
            "affected_modules": modules,
            "affected_tests": self.affected_tests(root, config, sorted(affected_files)),
            "relations": [{"source": r.source, "target": r.target, "kind": r.kind, "path": r.path, "line": r.line, "confidence": r.confidence, "resolved": r.resolved} for r in selected],
            "relation_hops": relation_hops,
            "max_hops": max_hops,
            "max_relations": max_relations,
        }

    def affected_tests(self, root: Path, config: ProjectConfig, files: list[str]) -> list[str]:
        modules = {_module_for(path) for path in files}
        stems = {Path(path).stem.lower() for path in files}
        tests = []
        for item in self.discover(root, config):
            lower = item.path.lower()
            if "test" in lower or "spec" in lower:
                if item.module in modules or any(stem and stem in lower for stem in stems) or files:
                    tests.append(item.path)
        return sorted(tests)

    def entrypoints(self, root: Path, config: ProjectConfig, limit: int = 200) -> list[dict[str, object]]:
        if limit < 1:
            raise ValueError("entrypoint limit must be at least 1")
        candidates: list[dict[str, object]] = []
        entrypoint_names = {"main.lua", "bootstrap.lua", "start.lua", "launcher.lua", "server.lua"}
        for indexed_file in self.discover(root, config):
            if indexed_file.language != "Lua":
                continue
            result = self.parse(root, indexed_file)
            lines = read_text(root / indexed_file.path).splitlines()
            seen_path = False
            for relation in result.relations:
                if relation.kind not in {"service_start", "dispatch"}:
                    continue
                seen_path = True
                line = relation.line or 1
                evidence = lines[line - 1].strip() if line <= len(lines) else relation.kind
                candidates.append({
                    "kind": "skynet_start" if relation.kind == "service_start" else "protocol_dispatch",
                    "path": indexed_file.path,
                    "line": line,
                    "symbol": relation.source,
                    "target": relation.target,
                    "evidence": evidence,
                    "confidence": relation.confidence,
                })
            if Path(indexed_file.path).name.lower() in entrypoint_names and not seen_path:
                candidates.append({
                    "kind": "inferred_file_entrypoint",
                    "path": indexed_file.path,
                    "line": 1,
                    "symbol": f"{indexed_file.path}::<module>",
                    "target": Path(indexed_file.path).stem,
                    "evidence": "入口文件名推断：需要现场验证启动命令。",
                    "confidence": 0.55,
                })
        unique: dict[tuple[object, ...], dict[str, object]] = {}
        for item in candidates:
            key = (item["path"], item["line"], item["kind"], item["target"])
            unique[key] = item
        return sorted(
            unique.values(),
            key=lambda item: (str(item["path"]), int(item["line"]), str(item["kind"])),
        )[:limit]

    def status(self) -> dict[str, object]:
        return {
            "engine": "builtin",

            "capabilities": ["initialize", "sync", "symbols", "search_symbols", "get_source", "trace", "impact", "affected_tests", "imports", "calls", "inheritance", "routes", "lua-skynet-evidence", "config-schema"],
            "precise_languages": ["Python", "Lua/Skynet", "SQL", "Configuration"],
            "conservative_languages": sorted(set(LANGUAGES.values()) - {"Python", "Lua"}),
            "limitations": ["dynamic dispatch", "reflection", "runtime dependency injection", "Lua metatables and generated protocol names require live verification"],
            "adapter": "builtin",
            "adapter_version": "0.1",
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
        self.symbol_ids: set[str] = set()

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
        self.symbol_ids.add(module_id)
        self.scope_ids.append(module_id)
        self.visit(tree)
        self.scope_ids.pop()
        return self.result

    def _qualname(self, name: str) -> str:
        return ".".join([*self.scope, name])

    def _unique_symbol_id(self, base_id: str, line: int) -> str:
        symbol_id = base_id if base_id not in self.symbol_ids else f"{base_id}@{line}"
        suffix = 2
        while symbol_id in self.symbol_ids:
            symbol_id = f"{base_id}@{line}.{suffix}"
            suffix += 1
        self.symbol_ids.add(symbol_id)
        return symbol_id

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
        symbol_id = self._unique_symbol_id(f"{self.path}::{qualname}", node.lineno)
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
        symbol_id = self._unique_symbol_id(f"{self.path}::{qualname}", node.lineno)
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

class LuaParser:
    "Conservative Lua/Skynet parser with explicit framework evidence."

    FUNCTION_PATTERN = re.compile(r"^\s*(?:local\s+)?function\s+([A-Za-z_][\w]*(?:[.:][A-Za-z_][\w]*)*)\s*\(")
    REQUIRE_PATTERN = re.compile(r"\brequire\s*(?:\(\s*)?['\"]([^'\"]+)['\"]\s*\)?")
    CLASS_PATTERN = re.compile(r"\bclass\s*\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*([^\)]+))?\)")
    CALL_PATTERN = re.compile(r"\b([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)\s*\(")

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text
        self.lines = text.splitlines()
        self.result = ParseResult(parser="lua-skynet")
        self.symbol_ids: set[str] = set()
        self.current = f"{path}::<module>"

    def parse(self) -> ParseResult:
        module_id = f"{self.path}::<module>"
        self.symbol_ids.add(module_id)
        self.result.symbols.append(Symbol(module_id, Path(self.path).stem, "module", self.path, 1, len(self.lines), source_hash=hash_text(self.text)))
        for number, line in enumerate(self.lines, 1):
            function = self.FUNCTION_PATTERN.search(line)
            if function:
                name = function.group(1)
                base_id = f"{self.path}::{name}"
                symbol_id = base_id
                suffix = 2
                while symbol_id in self.symbol_ids:
                    symbol_id = f"{base_id}@{number}" if suffix == 2 else f"{base_id}@{number}.{suffix}"
                    suffix += 1
                self.symbol_ids.add(symbol_id)
                short_name = name.rsplit(".", 1)[-1].rsplit(":", 1)[-1]
                self.result.symbols.append(Symbol(symbol_id, short_name, "method" if ":" in name or "." in name else "function", self.path, number, signature=line.strip()[:240], source_hash=hash_text(line), confidence=0.9))
                self.current = symbol_id
            for match in self.REQUIRE_PATTERN.finditer(line):
                self.result.relations.append(Relation(self.current, match.group(1), "imports", self.path, number, 0.95))
            for match in self.CLASS_PATTERN.finditer(line):
                name, base = match.groups()
                class_id = f"{self.path}::{name}"
                if class_id not in self.symbol_ids:
                    self.symbol_ids.add(class_id)
                    self.result.symbols.append(Symbol(class_id, name, "class", self.path, number, signature=line.strip()[:240], source_hash=hash_text(line), confidence=0.75))
                if base:
                    self.result.relations.append(Relation(class_id, base.strip(), "inherits", self.path, number, 0.75))
            self._framework_relations(line, number)
            for call in self.CALL_PATTERN.finditer(line):
                target = call.group(1)
                if target.split(".")[0] not in {"if", "for", "while", "function", "return", "require"} and not target.startswith(("skynet", "cluster", "protocol")):
                    self.result.relations.append(Relation(self.current, target, "calls", self.path, number, 0.45))
        return self.result

    def _framework_relations(self, line: str, number: int) -> None:
        patterns = [
            (r"\b(?:skynet|skynetx)\.start\s*\(", "service_start", None),
            (r"\b(?:skynet|skynetx)\.(?:newservice|uniqueservice)\s*\(\s*['\"]([^'\"]+)", "service_create", 1),
            (r"\b(?:skynet|skynetx)\.name\s*\(\s*['\"]([^'\"]+)", "service_name", 1),
            (r"\bskynet\.call\s*\(", "skynet_call", None),
            (r"\bskynet\.send\s*\(", "skynet_send", None),
            (r"\bcluster\.proxy\s*\(", "cluster_proxy", None),
            (r"\bcluster\.call\s*\(", "cluster_call", None),
            (r"\bcluster\.send\s*\(", "cluster_send", None),
            (r"\b(?:skynet|protocol)\.(?:dispatch|register_protocol|run|exec)\s*\(", "dispatch", None),
        ]
        for pattern, kind, group in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            target = match.group(group) if group else kind
            self.result.relations.append(Relation(self.current, target, kind, self.path, number, 0.85))

class ConfigParser:
    ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][\w.-]*)\s*=")

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text

    def parse(self) -> ParseResult:
        result = ParseResult(parser="config")
        for number, line in enumerate(self.text.splitlines(), 1):
            match = self.ASSIGNMENT.search(line)
            if match:
                name = match.group(1)
                result.symbols.append(Symbol(f"{self.path}::{name}", name, "config", self.path, number, signature=line.strip()[:240], source_hash=hash_text(line), confidence=0.9))
        return result

class SQLParser:
    TABLE = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?['\"]?([A-Za-z_][\w]*)['\"]?", re.IGNORECASE)
    INDEX = re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+['\"]?([A-Za-z_][\w]*)['\"]?\s+ON\s+['\"]?([A-Za-z_][\w]*)", re.IGNORECASE)

    def __init__(self, path: str, text: str):
        self.path = path
        self.text = text

    def parse(self) -> ParseResult:
        result = ParseResult(parser="sql")
        for number, line in enumerate(self.text.splitlines(), 1):
            table = self.TABLE.search(line)
            if table:
                name = table.group(1)
                result.symbols.append(Symbol(f"{self.path}::table.{name}", name, "table", self.path, number, signature=line.strip()[:240], source_hash=hash_text(line), confidence=0.9))
            index = self.INDEX.search(line)
            if index:
                name, table_name = index.groups()
                result.symbols.append(Symbol(f"{self.path}::index.{name}", name, "index", self.path, number, signature=line.strip()[:240], source_hash=hash_text(line), confidence=0.85))
                result.relations.append(Relation(f"{self.path}::index.{name}", f"{self.path}::table.{table_name}", "references", self.path, number, 0.85))
        return result


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
        symbol_ids = {module_id}
        current = module_id
        for number, line in enumerate(lines, 1):
            for kind, pattern in self.DEFINITION_PATTERNS:
                match = pattern.search(line)
                if match:
                    name = match.group(1)
                    base_id = f"{self.path}::{name}"
                    current = base_id if base_id not in symbol_ids else f"{base_id}@{number}"
                    suffix = 2
                    while current in symbol_ids:
                        current = f"{base_id}@{number}.{suffix}"
                        suffix += 1
                    symbol_ids.add(current)
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
    if config.engine == "codegraph":
        from .codegraph import CodeGraphEngine
        return CodeGraphEngine(config)
    if config.engine != "builtin":
        raise ValueError(f"unsupported index engine: {config.engine}")
    return BuiltinCodeIndexEngine()
