from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from project_knowledge.versioning import read_project_version  # noqa: E402
from scripts.build_npm_package import build_npm_package  # noqa: E402


REQUIRED_MCP_TOOLS = {"knowledge_status", "knowledge_context", "knowledge_impact"}


def launcher_path(prefix: Path, *, platform: str = sys.platform) -> Path:
    prefix = prefix.resolve()
    return prefix / ("project-kb.cmd" if platform == "win32" else "bin/project-kb")


def validate_mcp_responses(responses: list[dict[str, Any]]) -> list[str]:
    by_id = {response.get("id"): response for response in responses}
    initialize = by_id.get(1, {}).get("result", {})
    if initialize.get("serverInfo", {}).get("name") != "project-knowledge":
        raise RuntimeError("MCP initialize did not return the project-knowledge server")
    tools = by_id.get(2, {}).get("result", {}).get("tools", [])
    names = {item.get("name") for item in tools if isinstance(item, dict)}
    missing = sorted(REQUIRED_MCP_TOOLS - names)
    if missing:
        raise RuntimeError("MCP tools/list is missing: " + ", ".join(missing))
    status = by_id.get(3, {}).get("result", {})
    if status.get("isError") is not False:
        raise RuntimeError("MCP knowledge_status returned an error")
    return sorted(name for name in names if isinstance(name, str) and name in REQUIRED_MCP_TOOLS)


def run_checked(
    command: list[str | Path],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        rendered = " ".join(str(part) for part in command)
        raise RuntimeError(
            f"command failed ({result.returncode}): {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def validate_npm_bootstrap(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    root = root.resolve()
    version = read_project_version(root)
    npm_command = shutil.which("npm")
    git_command = shutil.which("git")
    if not npm_command or not git_command:
        raise RuntimeError("npm and git must be available on PATH")

    build = build_npm_package(root, root / "dist" / "npm-package")
    with tempfile.TemporaryDirectory(prefix="project-kb-npm-e2e-") as directory:
        temporary = Path(directory)
        artifacts = temporary / "artifacts"
        prefix = temporary / "npm-prefix"
        runtime_home = temporary / "runtimes"
        project = temporary / "sample-project"
        artifacts.mkdir()
        project.mkdir()
        env = os.environ.copy()
        env.update({
            "PROJECT_KB_PYTHON": sys.executable,
            "PROJECT_KB_RUNTIME_HOME": str(runtime_home),
            "npm_config_audit": "false",
            "npm_config_fund": "false",
        })

        packed = run_checked(
            [npm_command, "pack", "--json", "--pack-destination", artifacts],
            cwd=Path(build["output"]),
            env=env,
        )
        pack_payload = json.loads(packed.stdout)
        if not isinstance(pack_payload, list) or len(pack_payload) != 1:
            raise RuntimeError("npm pack did not produce exactly one artifact")
        packed_files = {
            item.get("path")
            for item in pack_payload[0].get("files", [])
            if isinstance(item, dict)
        }
        required_package_files = {"README.md", "LICENSE", "package.json"}
        missing_package_files = sorted(required_package_files - packed_files)
        if missing_package_files:
            raise RuntimeError(
                "npm artifact is missing release documentation: "
                + ", ".join(missing_package_files)
            )
        tarball = artifacts / str(pack_payload[0]["filename"])

        run_checked(
            [npm_command, "install", "--global", "--prefix", prefix, tarball],
            cwd=temporary,
            env=env,
        )
        launcher = launcher_path(prefix)
        if not launcher.is_file():
            raise RuntimeError(f"installed project-kb launcher is missing: {launcher}")
        version_result = run_checked([launcher, "--version"], cwd=temporary, env=env)
        if version not in version_result.stdout:
            raise RuntimeError(f"installed launcher reported an unexpected version: {version_result.stdout}")
        invalid = subprocess.run(
            [str(launcher), "not-a-command"],
            cwd=temporary,
            env=env,
            text=True,
            capture_output=True,
        )
        if invalid.returncode != 2:
            raise RuntimeError(
                f"Node launcher did not preserve the Python CLI exit code 2: {invalid.returncode}"
            )

        run_checked([git_command, "init", "-q"], cwd=project, env=env)
        source = project / "src"
        source.mkdir()
        (source / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        (project / "README.md").write_text("# npm bootstrap fixture\n", encoding="utf-8")

        first_init = run_checked([launcher, "init", "--json"], cwd=project, env=env)
        first_payload = json.loads(first_init.stdout)
        if first_payload.get("action") != "init":
            raise RuntimeError("installed project-kb init did not return an init report")
        config_path = project / ".codex" / "config.toml"
        first_config = config_path.read_bytes()
        parsed = tomllib.loads(first_config.decode("utf-8"))
        server = parsed["mcp_servers"]["project_knowledge"]
        python_command = Path(server["command"]).resolve()
        codegraph_command = Path(server["env"]["CODEGRAPH_COMMAND"]).resolve()
        if runtime_home.resolve() not in python_command.parents:
            raise RuntimeError("Codex MCP Python command is outside the managed runtime")
        if prefix.resolve() not in codegraph_command.parents:
            raise RuntimeError("Codex MCP CodeGraph command is outside the npm installation")

        run_checked([launcher, "init", "--json"], cwd=project, env=env)
        if config_path.read_bytes() != first_config:
            raise RuntimeError("repeated init changed the owned Codex config")

        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "npm-bootstrap-validator", "version": version},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "knowledge_status", "arguments": {}},
            },
        ]
        mcp = subprocess.run(
            [str(launcher), "mcp", "--project", str(project)],
            cwd=project,
            env=env,
            input="".join(json.dumps(item, separators=(",", ":")) + "\n" for item in requests),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=180,
        )
        if mcp.returncode != 0:
            raise RuntimeError(f"installed MCP process failed:\n{mcp.stderr}")
        responses = [json.loads(line) for line in mcp.stdout.splitlines() if line.strip()]
        required_tools = validate_mcp_responses(responses)

        run_checked([launcher, "uninstall", "--json"], cwd=project, env=env)
        if "project-kb:codex-mcp" in config_path.read_text(encoding="utf-8"):
            raise RuntimeError("uninstall left the owned Codex MCP block behind")
        if "project-kb:instructions" in (project / "AGENTS.md").read_text(encoding="utf-8"):
            raise RuntimeError("uninstall left the owned AGENTS block behind")
        if not (project / ".project-kb" / "index.db").is_file():
            raise RuntimeError("uninstall removed project knowledge data")

        return {
            "status": "passed",
            "version": version,
            "npm_package": tarball.name,
            "release_docs_packed": True,
            "runtime_created": (runtime_home / version / ".complete").is_file(),
            "exit_code_forwarded": True,
            "codex_config_valid": True,
            "init_idempotent": True,
            "mcp_tools": required_tools,
            "knowledge_preserved": True,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the installed npm bootstrap and MCP workflow")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    report = validate_npm_bootstrap(args.root)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"npm bootstrap validation passed for {report['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
