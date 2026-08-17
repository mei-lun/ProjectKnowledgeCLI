from __future__ import annotations

import hashlib
import fnmatch
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .config import ProjectConfig


class CodeGraphError(RuntimeError):
    """CodeGraph CLI 未能完成请求。"""


@dataclass(frozen=True, slots=True)
class CodeGraphCommand:
    argv: tuple[str, ...]
    display: str


class CodeGraphCommandResolver:
    """Resolve the installed public CodeGraph CLI without reading its database."""

    DEFAULT_WINDOWS_ROOT = Path("/mnt/c/Users/mei/AppData/Local/codegraph/current")

    def __init__(self, configured: str = "") -> None:
        self.configured = configured.strip()

    def resolve(self) -> CodeGraphCommand:
        candidates: list[CodeGraphCommand] = []
        if self.configured:
            candidates.extend(self._from_value(self.configured))
        env_value = os.environ.get("CODEGRAPH_COMMAND", "").strip()
        if env_value and env_value != self.configured:
            candidates.extend(self._from_value(env_value))
        executable = shutil.which("codegraph")
        if executable:
            candidates.append(CodeGraphCommand((executable,), executable))
        windows_root = self.DEFAULT_WINDOWS_ROOT
        node = windows_root / "node.exe"
        script = windows_root / "lib" / "dist" / "bin" / "codegraph.js"
        if node.is_file() and script.is_file():
            candidates.append(CodeGraphCommand((str(node), "--disable-warning=ExperimentalWarning", self._windows_path(script)), "CodeGraph 1.5 bundled node"))
        cmd = windows_root / "bin" / "codegraph.cmd"
        if cmd.is_file():
            if os.name == "nt":
                candidates.append(CodeGraphCommand((str(cmd),), str(cmd)))
            elif shutil.which("cmd.exe"):
                candidates.append(CodeGraphCommand(("cmd.exe", "/d", "/s", "/c", self._windows_path(cmd)), str(cmd)))
        for candidate in candidates:
            if candidate.argv and (shutil.which(candidate.argv[0]) or Path(candidate.argv[0]).is_file()):
                return candidate
        raise CodeGraphError("未找到 CodeGraph CLI；请设置 CODEGRAPH_COMMAND 或 codegraph_command")

    def _from_value(self, value: str) -> list[CodeGraphCommand]:
        parts = shlex.split(value, posix=os.name != "nt")
        if os.name == "nt":
            parts = [
                part[1:-1]
                if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}
                else part
                for part in parts
            ]
        if not parts:
            return []
        if len(parts) == 1 and parts[0].lower().endswith(".cmd") and os.name != "nt":
            return [CodeGraphCommand(("cmd.exe", "/d", "/s", "/c", parts[0]), value)] if shutil.which("cmd.exe") else []
        return [CodeGraphCommand(tuple(parts), value)]

    @staticmethod
    def _windows_path(path: Path) -> str:
        text = str(path)
        if text.startswith("/mnt/") and len(text) > 6:
            drive = text[5].upper()
            return drive + ":\\" + text[7:].replace("/", "\\")
        return text


def _host_path(project: Path) -> str:
    return CodeGraphCommandResolver._windows_path(project.resolve())


def _node_identity(node: dict[str, Any]) -> str:
    value = node.get("id") or node.get("qualifiedName") or node.get("name")
    if not str(value or "").strip():
        raise CodeGraphError("CodeGraph node missing identity")
    return str(value).strip()


def _validated_project_path(value: str, root: Path) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized:
        return ""
    candidate = Path(normalized)
    if ".." in candidate.parts:
        raise CodeGraphError(f"CodeGraph path is outside project: {value}")
    project = root.resolve()
    resolved = candidate.resolve() if candidate.is_absolute() else (project / candidate).resolve()
    try:
        relative = resolved.relative_to(project)
    except ValueError as error:
        raise CodeGraphError(f"CodeGraph path is outside project: {value}") from error
    return relative.as_posix()


def _node_path(node: dict[str, Any], root: Path) -> str:
    return _validated_project_path(str(node.get("filePath", node.get("path", ""))), root)


class CodeGraphClient:
    def __init__(self, project: str | Path, config: ProjectConfig | None = None, *, runner=subprocess.run) -> None:
        self.project = Path(project).resolve()
        self.config = config or ProjectConfig()
        self.command = CodeGraphCommandResolver(getattr(self.config, "codegraph_command", "")).resolve()
        self.timeout = max(1, int(getattr(self.config, "codegraph_timeout_seconds", 120)))
        self._runner = runner

    @property
    def command_display(self) -> str:
        return self.command.display

    def _effective_argv(self, argv: list[str]) -> list[str]:
        """Pass CODEGRAPH_DIR through WSL when invoking the bundled Windows Node."""
        if os.name == "nt" or not argv or not argv[0].lower().endswith("node.exe"):
            return argv
        cmd = Path("/mnt/c/Windows/System32/cmd.exe")
        if not cmd.is_file():
            return argv
        windows_argv = [
            CodeGraphCommandResolver._windows_path(Path(value)) if value.startswith("/mnt/") else value
            for value in argv
        ]
        command_line = f'set "CODEGRAPH_DIR={getattr(self.config, "codegraph_dir", ".codegraph")}" && ' + subprocess.list2cmdline(windows_argv)
        return [str(cmd), "/d", "/s", "/c", command_line]

    def _run(self, command: str, args: Sequence[str] = (), *, input_text: str | None = None, json_output: bool = False) -> Any:
        argv = [*self.command.argv, command, *args]
        if json_output:
            argv.append("--json")
        effective_argv = self._effective_argv(argv)
        try:
            completed = self._runner(
                effective_argv,
                input=input_text,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self.timeout,
                check=False,
                env={**os.environ, "CODEGRAPH_DIR": getattr(self.config, "codegraph_dir", ".codegraph")},
            )
        except FileNotFoundError as error:
            raise CodeGraphError(f"CodeGraph 命令不存在：{self.command.display}") from error
        except subprocess.TimeoutExpired as error:
            raise CodeGraphError(f"CodeGraph 命令超时（{self.timeout}s）：{command}") from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise CodeGraphError(f"CodeGraph {command} 失败（退出码 {completed.returncode}）：{detail}")
        output = completed.stdout or ""
        if not json_output:
            return output
        try:
            return json.loads(output) if output.strip() else {}
        except json.JSONDecodeError as error:
            raise CodeGraphError(f"CodeGraph {command} 返回无效 JSON：{output[:240]}") from error

    def init(self, *, force: bool = False) -> dict[str, Any]:
        args = [_host_path(self.project)]
        if force:
            args.append("--force")
        return {"output": self._run("init", args), "project_path": _host_path(self.project)}

    def sync(self) -> dict[str, Any]:
        return {"output": self._run("sync", [_host_path(self.project), "--quiet"]), "project_path": _host_path(self.project)}

    def status(self) -> dict[str, Any]:
        return self._run("status", [_host_path(self.project)], json_output=True)

    def files(self) -> list[dict[str, Any]]:
        value = self._run("files", ["-p", _host_path(self.project)], json_output=True)
        return value if isinstance(value, list) else list(value.get("files", [])) if isinstance(value, dict) else []

    def snapshot(self) -> dict[str, Any]:
        """Return a deterministic snapshot using only CodeGraph public output."""
        normalized: list[dict[str, Any]] = []
        for item in self.files():
            raw_path = str(item.get("path", "")).replace("\\", "/")
            path = raw_path[2:] if raw_path.startswith("./") else raw_path
            if not path:
                continue
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise CodeGraphError(f"CodeGraph 文件路径越界：{path}")
            if not self._in_scope(path):
                continue
            language = str(item.get("language", "unknown")).strip().lower() or "unknown"
            content_hash = str(item.get("contentHash", item.get("hash", ""))).strip()
            if not content_hash:
                source = (self.project / path).resolve()
                try:
                    source.relative_to(self.project)
                except ValueError as error:
                    raise CodeGraphError(f"CodeGraph 文件路径越界：{path}") from error
                try:
                    content_hash = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
                except OSError as error:
                    raise CodeGraphError(f"无法计算文件哈希：{path}") from error
            normalized.append({
                "path": path,
                "language": language,
                "content_hash": content_hash,
                "module": str(item.get("module", "")).strip(),
                "symbols": list(item.get("symbols", [])) if isinstance(item.get("symbols", []), list) else [],
            })
        normalized.sort(key=lambda value: (value["path"], value["language"]))
        identity = [
            {"path": item["path"], "language": item["language"], "content_hash": item["content_hash"]}
            for item in normalized
        ]
        encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {"snapshot_id": hashlib.sha256(encoded).hexdigest(), "files": normalized}

    def _in_scope(self, path: str) -> bool:
        def matches(pattern: str) -> bool:
            return (
                fnmatch.fnmatch(path, pattern)
                or fnmatch.fnmatch("/" + path, pattern)
                or (pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]))
            )

        includes = list(getattr(self.config, "include", ["**/*"]))
        excludes = list(getattr(self.config, "exclude", []))
        return (not includes or any(matches(pattern) for pattern in includes)) and not any(
            matches(pattern) for pattern in excludes
        )

    def query(self, search: str, *, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
        args = [search, "-p", _host_path(self.project), "-l", str(limit)]
        if kind:
            args.extend(["-k", kind])
        value = self._run("query", args, json_output=True)
        return value if isinstance(value, list) else list(value.get("results", [])) if isinstance(value, dict) else []

    def callers(self, symbol: str, *, limit: int = 20) -> dict[str, Any]:
        return self._run("callers", [symbol, "-p", _host_path(self.project), "-l", str(limit)], json_output=True)

    def callees(self, symbol: str, *, limit: int = 20) -> dict[str, Any]:
        return self._run("callees", [symbol, "-p", _host_path(self.project), "-l", str(limit)], json_output=True)

    def impact(self, symbol: str, *, depth: int = 2) -> dict[str, Any]:
        return self._run("impact", [symbol, "-p", _host_path(self.project), "-d", str(depth)], json_output=True)

    def affected_tests(
        self,
        files: Sequence[str],
        *,
        depth: int = 5,
        test_filter: str | None = None,
    ) -> dict[str, Any]:
        args = [*files, "-p", _host_path(self.project), "-d", str(depth)]
        if test_filter:
            args.extend(["-f", test_filter])
        return self._run("affected", args, json_output=True)

    def source(self, path: str, *, start_line: int = 1, limit: int = 200) -> str:
        return str(self._run("node", ["-p", _host_path(self.project), "-f", path, "--offset", str(start_line), "--limit", str(limit)]))


class CodeGraphEngine:
    """Code facts provided exclusively by the public CodeGraph CLI."""

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.client: CodeGraphClient | None = None
        self._diagnostic: dict[str, Any] | None = None
        self._symbol_references: dict[str, str] = {}

    def _client(self, root: Path) -> CodeGraphClient:
        if self.client is None or self.client.project != root.resolve():
            self.client = CodeGraphClient(root, self.config)
            self._symbol_references.clear()
        return self.client

    def snapshot(self, root: Path, config: ProjectConfig):
        from .engine import CodeIndexSnapshot, IndexedFile, _module_for

        payload = self._client(root).snapshot()
        result: list[IndexedFile] = []
        for item in payload["files"]:
            path = _validated_project_path(str(item.get("path", "")), root)
            if not path:
                continue
            language = str(item.get("language", "unknown")).lower()
            language_names = {"lua": "Lua", "luau": "Lua", "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript"}
            source = root / path
            try:
                stat = source.stat()
                content_hash = str(item.get("content_hash", ""))
                size = int(item.get("size", stat.st_size) or stat.st_size)
                mtime_ns = stat.st_mtime_ns
            except OSError:
                continue
            result.append(IndexedFile(path, language_names.get(language, language.title()), _module_for(path), size, mtime_ns, content_hash))
        return CodeIndexSnapshot(
            snapshot_id=str(payload["snapshot_id"]),
            files=tuple(sorted(result, key=lambda item: item.path)),
        )

    def initialize(self, root: Path, config: ProjectConfig):
        return self._client(root).init()

    def sync(self, root: Path, config: ProjectConfig, previous=None):
        return self._client(root).sync()

    def status(self):
        if self._diagnostic is not None:
            return dict(self._diagnostic)
        return self._diagnostic_result(False, "not_probed", details="CodeGraph has not been probed for this project")

    def diagnose(self, root: Path) -> dict[str, Any]:
        try:
            client = self._client(root)
        except CodeGraphError as error:
            self._diagnostic = self._diagnostic_result(False, "cli_missing", details=str(error))
            return dict(self._diagnostic)
        try:
            payload = client.status()
        except CodeGraphError as error:
            self._diagnostic = self._diagnostic_result(
                False, "command_failed", command=client.command_display, details=str(error)
            )
            return dict(self._diagnostic)
        initialized = bool(payload.get("initialized")) if isinstance(payload, dict) else False
        version = str(payload.get("version", "unknown")) if isinstance(payload, dict) else "unknown"
        self._diagnostic = self._diagnostic_result(
            initialized,
            "available" if initialized else "project_not_initialized",
            command=client.command_display,
            adapter_version=version,
            details="" if initialized else "CodeGraph is installed but this project is not initialized",
        )
        self._diagnostic["codegraph"] = payload
        return dict(self._diagnostic)

    @staticmethod
    def _diagnostic_result(
        available: bool,
        reason_code: str,
        *,
        command: str = "",
        adapter_version: str = "unknown",
        details: str = "",
    ) -> dict[str, Any]:
        return {
            "engine": "codegraph",
            "adapter": "codegraph-public-cli",
            "adapter_version": adapter_version,
            "available": available,
            "reason_code": reason_code,
            "command": command,
            "details": details,
            "capabilities": [
                "initialize", "sync", "snapshot", "symbols", "search_symbols", "get_source",
                "trace", "impact", "affected_tests", "calls",
            ],
            "limitations": [
                "requires an initialized CodeGraph project",
                "uses only the public CodeGraph CLI contract",
                "does not fall back to local source parsing",
            ],
        }

    def search_symbols(self, root: Path, config: ProjectConfig, query: str, limit: int = 20):
        from .models import Symbol
        symbols = []
        for item in self._client(root).query(query, limit=limit):
            node = item.get("node", item)
            identity = _node_identity(node)
            self._symbol_references[identity] = str(
                node.get("qualifiedName") or node.get("name") or identity
            )
            symbols.append(Symbol(
                id=identity,
                name=str(node.get("name", "")), kind=str(node.get("kind", "unknown")),
                path=_node_path(node, root), line=int(node.get("startLine", 1) or 1),
                end_line=int(node.get("endLine", 0) or 0) or None, signature=str(node.get("signature", "") or ""),
                source_hash="", confidence=1.0,
            ))
        return symbols

    def get_source(self, root: Path, path: str, start_line=None, end_line=None) -> str:
        if start_line is None:
            start_line = 1
        limit = max(1, (end_line or start_line + 199) - start_line + 1)
        return self._client(root).source(path, start_line=start_line, limit=limit)

    def trace(self, root: Path, symbol_id: str, config: ProjectConfig, max_depth=1, limit=200):
        from .models import Relation
        result: list[Relation] = []
        name = self._symbol_references.get(
            symbol_id, symbol_id.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        )
        for direction, payload_key in (("callers", "callers"), ("callees", "callees")):
            payload = self._client(root).callers(name, limit=limit) if direction == "callers" else self._client(root).callees(name, limit=limit)
            for item in payload.get(payload_key, []) if isinstance(payload, dict) else []:
                node = item.get("node", item)
                node_id = _node_identity(node)
                source, target = (
                    (node_id, symbol_id) if direction == "callers" else (symbol_id, node_id)
                )
                result.append(Relation(source, target, "calls", _node_path(node, root), node.get("startLine"), 1.0, True))
        return result[:limit]

    def impact(self, root: Path, config: ProjectConfig, files=None, symbols=None, max_hops=1, max_relations=500):
        from .engine import _module_for

        client = self._client(root)
        affected_files = {_validated_project_path(path, root) for path in (files or [])}
        affected_symbols: set[str] = set()
        relations: list[dict[str, Any]] = []
        relation_hops: dict[str, int] = {}
        for anchor in symbols or []:
            reference = self._symbol_references.get(
                anchor, anchor.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
            )
            payload = client.impact(reference, depth=max_hops)
            nodes = payload.get("affected", []) if isinstance(payload, dict) else []
            for item in nodes:
                node = item.get("node", item) if isinstance(item, dict) else {}
                target = _node_identity(node)
                path = _node_path(node, root)
                affected_symbols.add(target)
                if path:
                    affected_files.add(path)
                relations.append({
                    "source": anchor,
                    "target": target,
                    "kind": str(node.get("relation", node.get("kind", "affected"))),
                    "path": path,
                    "line": node.get("startLine"),
                    "confidence": 1.0,
                    "resolved": True,
                })
                if len(relations) >= max_relations:
                    break
            if nodes:
                relation_hops[str(max_hops)] = relation_hops.get(str(max_hops), 0) + min(len(nodes), max_relations)
            if len(relations) >= max_relations:
                break
        ordered_files = sorted(path for path in affected_files if path)
        raw_tests = self.affected_tests(root, config, ordered_files) if ordered_files else []
        affected_test_paths: set[str] = set()
        for item in raw_tests:
            value = (
                str(item.get("filePath", item.get("path", item.get("id", ""))))
                if isinstance(item, dict)
                else str(item)
            )
            path = _validated_project_path(value, root)
            if path:
                affected_test_paths.add(path)
        return {
            "affected_files": ordered_files,
            "affected_symbols": sorted(affected_symbols),
            "affected_modules": sorted({_module_for(path) for path in ordered_files}),
            "affected_tests": sorted(affected_test_paths),
            "relations": relations[:max_relations],
            "relation_hops": relation_hops,
            "max_hops": max_hops,
            "max_relations": max_relations,
        }

    def affected_tests(self, root: Path, config: ProjectConfig, files):
        client = self._client(root)
        tests: list[str] = []
        for test_filter in (None, "tests/**", "**/*test*", "**/*spec*"):
            payload = client.affected_tests(files, test_filter=test_filter)
            if isinstance(payload, dict):
                tests.extend(str(path) for path in payload.get("affectedTests", []))
            if tests:
                break
        return sorted(set(tests))

