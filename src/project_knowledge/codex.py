from __future__ import annotations

import os
import re
import shutil
import tomllib
import json
import subprocess
import queue
import threading
import time
import signal
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import atomic_write, read_text


CODEX_MCP_MARKER = "codex-mcp"
MCP_VERIFICATION_TIMEOUT = 120.0


def resolve_codegraph_command() -> Path:
    configured = os.environ.get("CODEGRAPH_COMMAND")
    discovered = shutil.which(configured or "codegraph")
    if discovered:
        return Path(discovered).resolve()
    if configured and Path(configured).is_absolute():
        return Path(configured).resolve()
    raise ValueError(
        "CodeGraph executable is unavailable; set CODEGRAPH_COMMAND to an absolute path"
    )


def resolve_project_launcher() -> Path:
    configured = os.environ.get("PROJECT_KB_LAUNCHER")
    candidate = configured or shutil.which("project-kb") or shutil.which("project-kb.cmd")
    if candidate:
        resolved = Path(candidate).resolve()
        if "runtimes" not in {part.lower() for part in resolved.parts}:
            return resolved
    # The command name is a valid fallback for npm shims and virtualenv scripts.
    return Path("project-kb")


def codex_mcp_body(root: Path, codegraph_command: Path | None = None, launcher: Path | None = None) -> str:
    root = Path(root).resolve()
    launcher = launcher or resolve_project_launcher()
    codegraph_command = codegraph_command or _optional_codegraph_command()
    lines = [
        "[mcp_servers.project_knowledge]",
        f"command = {json.dumps(str(launcher), ensure_ascii=False)}",
        'args = ["mcp", "--project", "."]',
        f"cwd = {json.dumps(str(root), ensure_ascii=False)}",
        "enabled = true",
        f"startup_timeout_sec = {int(MCP_VERIFICATION_TIMEOUT)}",
    ]
    if codegraph_command:
        lines.extend(["", "[mcp_servers.project_knowledge.env]", f"CODEGRAPH_COMMAND = {json.dumps(str(codegraph_command), ensure_ascii=False)}"])
    return "\n".join(lines)


def _optional_codegraph_command() -> Path | None:
    try:
        return resolve_codegraph_command()
    except ValueError:
        return None


def update_codex_config(path: Path, body: str | None) -> bool:
    start = f"# project-kb:{CODEX_MCP_MARKER}:start"
    end = f"# project-kb:{CODEX_MCP_MARKER}:end"
    current = read_text(path) if path.exists() else ""
    pattern = re.compile(
        rf"^{re.escape(start)}\r?\n"
        rf"\[mcp_servers\.project_knowledge\]\r?\n"
        rf".*?^{re.escape(end)}(?:\r?\n)?",
        re.DOTALL | re.MULTILINE,
    )
    matches = pattern.findall(current)
    if len(matches) > 1:
        raise ValueError("Codex TOML contains multiple project-kb owned MCP blocks")

    unowned = pattern.sub("", current, count=1)
    parsed_unowned = _parse_toml(unowned)
    if _project_knowledge_server(parsed_unowned) is not None:
        raise ValueError(
            "Codex TOML already defines unowned mcp_servers.project_knowledge"
        )

    replacement = "" if body is None else f"{start}\n{body.rstrip()}\n{end}\n"
    if matches:
        updated = pattern.sub(lambda _match: replacement, current, count=1)
    elif body is None:
        return False
    else:
        updated = current.rstrip() + ("\n\n" if current.strip() else "") + replacement

    _parse_toml(updated)
    if updated == current:
        return False
    atomic_write(path, updated)
    return True


def _parse_toml(content: str) -> dict[str, object]:
    try:
        return tomllib.loads(content) if content.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"Codex config is not valid TOML: {error}") from error


def _project_knowledge_server(parsed: dict[str, object]) -> object | None:
    servers = parsed.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    return servers.get("project_knowledge")


def codex_config_path() -> Path:
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return home / "config.toml"


def migrate_legacy_codex(root: Path, *, dry_run: bool = False, user_config: Path | None = None) -> dict[str, Any]:
    """Move only project-owned legacy global entries after project setup succeeds."""
    root = Path(root).resolve()
    config = user_config or codex_config_path()
    if not config.exists():
        return {"action": "migrate", "changed": False, "removed": [], "backup": None}
    current = read_text(config)
    parsed = _parse_toml(current)
    servers = parsed.get("mcp_servers")
    if not isinstance(servers, dict):
        return {"action": "migrate", "changed": False, "removed": [], "backup": None}
    removable: list[str] = []
    marked_names = set(re.findall(r"#\s*project-kb:codex-mcp:start[\s\S]*?\[mcp_servers\.([^\.\]]+)", current))
    for name, value in servers.items():
        if not (name == "project_knowledge" or str(name).startswith("project_knowledge_")) or not isinstance(value, dict):
            continue
        command = str(value.get("command", ""))
        args = value.get("args", [])
        if not _is_project_kb_command(command, args):
            continue
        target = _legacy_project_path(value)
        if (target and target == root) or (str(name) in marked_names and str(name) == "project_knowledge"):
            removable.append(str(name))
    if not removable:
        return {"action": "migrate", "changed": False, "removed": [], "backup": None}
    backup = config.with_name(f"config.toml.project-kb-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    if dry_run:
        return {"action": "migrate", "dry_run": True, "changed": True, "removed": removable, "backup": str(backup)}
    atomic_write(backup, current)
    try:
        updated = _remove_server_tables(current, removable)
        _parse_toml(updated)
        atomic_write(config, updated)
    except Exception:
        atomic_write(config, current)
        raise
    return {"action": "migrate", "changed": True, "removed": removable, "backup": str(backup), "restart_required": True}


def _is_project_kb_command(command: str, args: object) -> bool:
    normalized = command.replace("\\", "/").lower()
    if normalized.endswith("project-kb") or normalized.endswith("project-kb.cmd"):
        return True
    if "python" in normalized or normalized.endswith("python.exe"):
        return isinstance(args, list) and any(str(item) == "project_knowledge" for item in args) and any(str(item) == "mcp" for item in args)
    return False


def _legacy_project_path(value: dict[str, object]) -> Path | None:
    cwd = value.get("cwd")
    args = value.get("args")
    if isinstance(cwd, str):
        return Path(cwd).resolve()
    if isinstance(args, list):
        for index, item in enumerate(args[:-1]):
            if str(item) in {"--project", "--project-path"}:
                return Path(str(args[index + 1])).resolve()
    return None


def _remove_server_tables(content: str, names: list[str]) -> str:
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    skip = False
    for line in lines:
        if line.strip() in {"# project-kb:codex-mcp:start", "# project-kb:codex-mcp:end"}:
            continue
        match = re.match(r"^\[mcp_servers\.([^\.\]]+)(?:\.[^\]]+)?\]", line.strip())
        if match:
            skip = match.group(1) in names
        if not skip:
            out.append(line)
    return "".join(out).strip() + ("\n" if out else "")


REQUIRED_MCP_TOOLS = {"knowledge_status", "knowledge_context", "knowledge_impact"}
VERIFICATION_PROTOCOL = "2026-07-28"


def validate_mcp_response(response: object, request_id: int) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
        raise ValueError("invalid JSON-RPC response")
    if type(response.get("id")) is not int or response["id"] != request_id:
        raise ValueError(f"unexpected response ID; expected {request_id}")
    if "error" in response:
        raise ValueError(f"MCP request failed: {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError("MCP response result must be an object")
    if request_id == 1:
        info = result.get("serverInfo")
        capabilities = result.get("capabilities")
        if (result.get("protocolVersion") != VERIFICATION_PROTOCOL
                or not isinstance(info, dict) or info.get("name") != "project-knowledge"
                or not isinstance(info.get("version"), str)
                or not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict)):
            raise ValueError("MCP initialize returned an invalid handshake")
    elif request_id == 2:
        items = result.get("tools")
        if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in items):
            raise ValueError("MCP tools/list result.tools must be a list of tools")
        missing = REQUIRED_MCP_TOOLS - {item["name"] for item in items}
        if missing:
            raise ValueError("MCP tools/list is missing: " + ", ".join(sorted(missing)))
    else:
        name = "knowledge_status" if request_id == 3 else "knowledge_context"
        if result.get("isError", False) is not False:
            raise ValueError(f"MCP {name} returned an error: {result.get('content')}")
        payload = result.get("structuredContent")
        if payload is None:
            content = result.get("content")
            if isinstance(content, list):
                text = next((item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"), None)
                if isinstance(text, str):
                    payload = json.loads(text)
        if not isinstance(payload, dict) or not payload or payload.get("error"):
            raise ValueError(f"MCP {name} returned an invalid query payload")
    return result


def verify_project_mcp(root: Path, *, launcher: Path | None = None, timeout: float = MCP_VERIFICATION_TIMEOUT, env: dict[str, str] | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    report: dict[str, Any] = {
        "attempted": False, "verified": False, "stage": "configuration", "tools": [],
        "server_started": False, "handshake_verified": False, "tools_verified": False,
        "queries_verified": False, "codex_session_verified": False,
    }
    process = None
    readers: list[threading.Thread] = []
    stderr: deque[str] = deque(maxlen=20)
    deadline = time.monotonic() + timeout
    try:
        config = root / ".codex" / "config.toml"
        if config.exists():
            server = _project_knowledge_server(_parse_toml(read_text(config)))
        else:
            server = {"command": str(launcher or resolve_project_launcher()), "args": ["mcp", "--project", "."], "cwd": str(root)}
        if not isinstance(server, dict):
            raise ValueError("project Codex config has no project_knowledge server")
        command, args = server.get("command"), server.get("args", [])
        cwd, overrides = server.get("cwd", str(root)), server.get("env", {})
        if not isinstance(command, str) or not Path(command).is_absolute() or not Path(command).is_file():
            raise ValueError("stable launcher is missing; reinstall project-kb or set PROJECT_KB_LAUNCHER to its absolute path")
        if server.get("enabled", True) is not True:
            raise ValueError("project_knowledge MCP server is disabled")
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise ValueError("MCP args must be a list of strings")
        if not isinstance(cwd, str) or not isinstance(overrides, dict) or any(not isinstance(value, str) for value in overrides.values()):
            raise ValueError("MCP cwd and env must contain strings")
        workdir = Path(cwd)
        if not workdir.is_absolute():
            workdir = root / workdir
        report["stage"] = "launch"
        report["attempted"] = True
        process = subprocess.Popen(
            [command, *args], cwd=workdir, env={**(os.environ if env is None else env), **overrides},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            shell=Path(command).suffix.lower() in {".cmd", ".bat"},
            start_new_session=os.name != "nt",
        )
        report["server_started"] = True
        incoming: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            try:
                for line in process.stdout:
                    incoming.put(line)
            finally:
                incoming.put(None)

        def read_stderr() -> None:
            for line in process.stderr:
                stderr.append(line)

        for target in (read_stdout, read_stderr):
            reader = threading.Thread(target=target, daemon=True)
            reader.start()
            readers.append(reader)

        def send(request: dict[str, Any]) -> None:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()

        def receive(request_id: int) -> dict[str, Any]:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("MCP verification timed out")
                try:
                    line = incoming.get(timeout=remaining)
                except queue.Empty as error:
                    raise TimeoutError("MCP verification timed out") from error
                if line is None:
                    raise ValueError(f"MCP process closed before response {request_id}")
                response = json.loads(line)
                if isinstance(response, dict) and "id" not in response and response.get("jsonrpc") == "2.0" and isinstance(response.get("method"), str):
                    continue
                return validate_mcp_response(response, request_id)

        steps = [
            ("initialize", "initialize", {"protocolVersion": VERIFICATION_PROTOCOL, "capabilities": {}, "clientInfo": {"name": "project-kb-verifier", "version": "1"}}),
            ("tools/list", "tools/list", {}),
            ("knowledge_status", "tools/call", {"name": "knowledge_status", "arguments": {}}),
            ("knowledge_context", "tools/call", {"name": "knowledge_context", "arguments": {"task": "Verify project knowledge installation", "maxTokens": 256}}),
        ]
        for request_id, (stage, method, params) in enumerate(steps, 1):
            report["stage"] = stage
            send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            result = receive(request_id)
            if request_id == 1:
                report["handshake_verified"] = True
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            elif request_id == 2:
                report["tools_verified"] = True
                report["tools"] = sorted(item["name"] for item in result["tools"])
        report["queries_verified"] = True
        report["stage"] = "shutdown"
        process.stdin.close()
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise ValueError(f"MCP process exited with code {process.returncode}")
        report.update(verified=True, stage="complete", next_action="Restart Codex and verify tools in a new project session")
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        report.update(reason=str(error), next_action="Inspect .codex/config.toml command/args/cwd/env and the reported stage, repair the cause, then rerun project-kb install")
    finally:
        if process is not None:
            if process.poll() is None:
                # npm shims launch descendants that also hold the stdio pipes open.
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5, check=False)
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except (OSError, subprocess.SubprocessError):
                    pass
                if process.poll() is None:
                    process.kill()
                process.wait()
            for reader in readers:
                reader.join(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        report["stderr"] = "".join(stderr)[-1000:] or None
    return report
