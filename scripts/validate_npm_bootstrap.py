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
from project_knowledge.codex import REQUIRED_MCP_TOOLS, validate_mcp_response, verify_project_mcp  # noqa: E402
from scripts.build_npm_package import build_npm_package  # noqa: E402


def launcher_path(prefix: Path, *, platform: str = sys.platform) -> Path:
    prefix = prefix.resolve()
    return prefix / ("project-kb.cmd" if platform == "win32" else "bin/project-kb")


def validate_mcp_responses(responses: list[dict[str, Any]]) -> list[str]:
    by_id = {}
    for response in responses:
        request_id = response.get("id")
        if type(request_id) is not int or request_id not in {1, 2, 3, 4}:
            raise RuntimeError("MCP returned an unexpected response ID")
        if request_id in by_id:
            raise RuntimeError("MCP returned a duplicate response ID")
        by_id[request_id] = response
    try:
        for request_id in range(1, 5):
            validate_mcp_response(by_id.get(request_id), request_id)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    return sorted(REQUIRED_MCP_TOOLS)


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
        codex_home = temporary / "codex-home"
        pi_agent_dir = temporary / "pi-agent"
        project = temporary / "sample-project"
        artifacts.mkdir()
        project.mkdir()
        env = os.environ.copy()
        # Validate the installed package, without inheriting a source checkout override.
        env.pop("PYTHONPATH", None)
        env.update({
            "PROJECT_KB_PYTHON": sys.executable,
            "CODEX_HOME": str(codex_home),
            "PI_CODING_AGENT_DIR": str(pi_agent_dir),
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
        env.pop("PROJECT_KB_LAUNCHER", None)
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

        global_install = run_checked(
            [launcher, "agent", "install", "--global", "--target", "pi", "--json"],
            cwd=temporary,
            env=env,
        )
        global_payload = json.loads(global_install.stdout)
        global_config = codex_home / "config.toml"
        if global_config.exists():
            raise RuntimeError("global agent install unexpectedly wrote Codex config")
        if not (pi_agent_dir / "extensions" / "project-kb.ts").is_file():
            raise RuntimeError("global Pi extension is missing")
        doctor = run_checked(
            [launcher, "doctor", "--global", "--json"],
            cwd=temporary,
            env=env,
        )
        doctor_payload = json.loads(doctor.stdout)
        if doctor_payload.get("codexConfigured") or not doctor_payload.get("piConfigured"):
            raise RuntimeError("doctor --global did not report Pi-only integration")

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
        if not Path(server.get("command", "")).is_absolute() or Path(server["command"]).resolve() != launcher.resolve():
            raise RuntimeError("project Codex MCP config does not use the stable project-kb launcher")
        if server.get("args") != ["mcp", "--project", "."]:
            raise RuntimeError("project Codex MCP config does not use the stable project-relative arguments")
        if Path(server.get("cwd", "")).resolve() != project.resolve():
            raise RuntimeError("project Codex MCP config does not use the project working directory")
        if not isinstance(server.get("env", {}), dict):
            raise RuntimeError("project Codex MCP environment must be a table")
        if not first_payload.get("codex", {}).get("verification", {}).get("queries_verified"):
            raise RuntimeError("init did not verify the installed MCP queries")

        run_checked([launcher, "init", "--json"], cwd=project, env=env)
        if config_path.read_bytes() != first_config:
            raise RuntimeError("repeated init changed the owned Codex config")

        verification = verify_project_mcp(project, timeout=180, env=env)
        if not verification["verified"]:
            raise RuntimeError(f"installed MCP verification failed: {json.dumps(verification)}")
        required_tools = sorted(REQUIRED_MCP_TOOLS & set(verification["tools"]))

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
            "exit_code_forwarded": True,
            "codex_config_valid": True,
            "global_agent_install": True,
            "pi_extension_present": True,
            "doctor_global_valid": True,
            "init_idempotent": True,
            "mcp_tools": required_tools,
            "mcp_queries_verified": verification["queries_verified"],
            "codex_session_verified": False,
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
