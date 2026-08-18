from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from project_knowledge import __version__


def validate_evaluation_report(
    report_path: str | Path,
    project_root: str | Path | None = None,
) -> tuple[bool, list[str]]:
    """Validate that the active evaluation report describes this release.

    Historical reports intentionally remain in the repository, but the active
    report must not silently describe an older package or a removed local
    parser. This guard is deliberately independent of metric thresholds so it
    catches provenance drift before a quality result is interpreted.
    """

    path = Path(report_path)
    errors: list[str] = []
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return False, [f"evaluation report cannot be read: {error}"]
    if not isinstance(payload, dict):
        return False, ["evaluation report must be a JSON object"]

    package_version = str(payload.get("package_version", "")).strip()
    if package_version != __version__:
        errors.append(f"report package version {package_version or '<missing>'} does not match {__version__}")

    strategies = payload.get("strategies")
    codegraph = strategies.get("codegraph") if isinstance(strategies, dict) else None
    if not isinstance(codegraph, dict):
        errors.append("report must contain a codegraph strategy")
    else:
        if codegraph.get("available") is not True:
            errors.append("CodeGraph strategy must be available")
        engine = codegraph.get("reproducibility", {}).get("engine", {})
        if not isinstance(engine, dict):
            engine = {}
        if engine.get("available") is not True:
            errors.append("CodeGraph reproducibility engine must be available")
        if engine.get("adapter") != "codegraph-public-cli":
            errors.append("CodeGraph report must use codegraph-public-cli")

    serialized = json.dumps(payload, ensure_ascii=False).lower()
    if "adapter_unavailable" in serialized:
        errors.append("active report must not contain adapter_unavailable")
    if "builtin" in serialized:
        errors.append("active report must not contain builtin")

    if project_root is not None:
        root = Path(project_root)
        expected_report = (root / "evaluation" / "reports" / "latest.json").resolve()
        if path.resolve() != expected_report:
            errors.append("active report path must be evaluation/reports/latest.json")

    return not errors, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate active evaluation report provenance")
    parser.add_argument("--report", default="evaluation/reports/latest.json")
    parser.add_argument("--project", default=".")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    valid, errors = validate_evaluation_report(args.report, args.project)
    result = {"passed": valid, "report": str(Path(args.report)), "errors": errors}
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("PASS" if valid else "FAIL", result)
    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
