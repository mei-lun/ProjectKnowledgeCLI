from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from project_knowledge.codegraph import CodeGraphClient, CodeGraphEngine
from project_knowledge.config import ProjectConfig


def _write_fixture(root: Path) -> None:
    for directory in ("src", "tests", "service", "config"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "src" / "helper.py").write_text(
        "def helper(value):\n    return value + 1\n", encoding="utf-8"
    )
    (root / "src" / "app.py").write_text(
        "from src.helper import helper\n\ndef run(value):\n    return helper(value)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_app.py").write_text(
        "from src.app import run\n\ndef test_run():\n    assert run(1) == 2\n",
        encoding="utf-8",
    )
    (root / "service" / "main.lua").write_text(
        'local skynet = require "skynet"\nskynet.start(function() end)\n',
        encoding="utf-8",
    )
    (root / "config" / "app.json").write_text('{"entry": "src.app:run"}\n', encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='codegraph-validation'\n", encoding="utf-8")


def validate_codegraph(command: str = "", keep_fixture: bool = False) -> dict[str, Any]:
    fixture = Path(tempfile.mkdtemp(prefix="project-kb-codegraph-"))
    _write_fixture(fixture)
    config = ProjectConfig(
        engine="codegraph",
        codegraph_command=command,
        codegraph_dir=str(fixture / ".codegraph"),
        codegraph_timeout_seconds=120,
    )
    report: dict[str, Any] = {
        "passed": False,
        "fixture_path": str(fixture),
        "adapter_version": "unknown",
        "command": command or "auto",
        "checks": [],
        "durations_ms": {},
        "counts": {},
        "failure": None,
    }
    try:
        client = CodeGraphClient(fixture, config)
        engine = CodeGraphEngine(config)
        engine.client = client
        report["command"] = client.command_display

        def check(name: str, operation: Callable[[], Any], predicate: Callable[[Any], bool]) -> Any:
            started = time.perf_counter()
            value = operation()
            report["durations_ms"][name] = round((time.perf_counter() - started) * 1000, 3)
            if not predicate(value):
                raise RuntimeError(f"CodeGraph validation check failed: {name}")
            report["checks"].append(name)
            return value

        check("init", client.init, lambda value: bool(value.get("project_path")))
        diagnostic = engine.diagnose(fixture)
        if not diagnostic.get("available"):
            raise RuntimeError(f"CodeGraph diagnostic failed: {diagnostic}")
        report["adapter_version"] = diagnostic.get("adapter_version", "unknown")

        files = check("files", client.files, lambda value: len(value) >= 4)
        symbols = check(
            "query",
            lambda: engine.search_symbols(fixture, config, "run", limit=20),
            lambda value: bool(value),
        )
        symbol_id = symbols[0].id
        relations = check(
            "trace",
            lambda: engine.trace(fixture, symbol_id, config, max_depth=2, limit=50),
            lambda value: bool(value),
        )
        impact = check(
            "impact",
            lambda: engine.impact(fixture, config, symbols=[symbol_id], max_hops=2),
            lambda value: bool(value.get("affected_files")),
        )
        affected = check(
            "affected",
            lambda: engine.affected_tests(fixture, config, ["src/app.py"]),
            lambda value: bool(value),
        )
        report["counts"] = {
            "files": len(files),
            "symbols": len(symbols),
            "relations": len(relations),
            "affected_files": len(impact.get("affected_files", [])),
            "affected_tests": len(affected),
        }
        report["passed"] = True
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        if not keep_fixture:
            shutil.rmtree(fixture, ignore_errors=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Project Knowledge against the public CodeGraph CLI")
    parser.add_argument("--command", default="", help="CodeGraph command override")
    parser.add_argument("--keep-fixture", action="store_true", help="preserve the temporary fixture for inspection")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit JSON")
    args = parser.parse_args(argv)
    report = validate_codegraph(args.command, args.keep_fixture)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("PASS" if report["passed"] else "FAIL", report)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
