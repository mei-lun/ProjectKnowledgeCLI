from __future__ import annotations

import os
import re
import shutil
import tomllib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import atomic_write, read_text


CODEX_MCP_MARKER = "codex-mcp"


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


def _jsonrpc_response_objects(value: object) -> list[dict[str, Any]]:
    """Return JSON-RPC response objects from a single or batched payload."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        responses: list[dict[str, Any]] = []
        for item in value:
            responses.extend(_jsonrpc_response_objects(item))
        return responses
    return []


def verify_project_mcp(root: Path, *, launcher: Path | None = None, timeout: float = 8.0) -> dict[str, Any]:
    launcher = launcher or resolve_project_launcher()
    if not launcher.is_absolute() or not launcher.exists():
        return {"attempted": False, "verified": False, "reason": "stable launcher is not available on PATH"}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2026-07-28"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    try:
        completed = subprocess.run(
            [str(launcher), "mcp", "--project", str(Path(root).resolve())],
            input="\n".join(json.dumps(item) for item in requests) + "\n",
            text=True, capture_output=True, timeout=timeout,
            cwd=str(Path(root).resolve()), check=False,
            shell=launcher.suffix.lower() in {".cmd", ".bat"},
        )
        responses = [
            response
            for line in completed.stdout.splitlines()
            if line.strip()
            for response in _jsonrpc_response_objects(json.loads(line))
        ]
        tools: set[str] = set()
        for response in responses:
            result = response.get("result")
            if not isinstance(result, dict):
                continue
            tools_result = result.get("tools")
            if not isinstance(tools_result, dict):
                continue
            tool_items = tools_result.get("tools")
            if not isinstance(tool_items, list):
                continue
            for item in tool_items:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    tools.add(item["name"])
        required = {"knowledge_status", "knowledge_context", "knowledge_impact"}
        verified = completed.returncode == 0 and required <= tools
        return {"attempted": True, "verified": verified, "tools": sorted(tools), "stderr": completed.stderr[-500:] if completed.stderr else None}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return {"attempted": True, "verified": False, "error": str(error)}
