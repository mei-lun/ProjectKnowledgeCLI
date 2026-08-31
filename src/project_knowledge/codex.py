from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path

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


def codex_mcp_body(root: Path, codegraph_command: Path | None = None) -> str:
    return "\n".join(
        [
            "[mcp_servers.project_knowledge]",
            'command = "project-kb"',
            'args = ["mcp", "--project", "."]',
        ]
    )


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
