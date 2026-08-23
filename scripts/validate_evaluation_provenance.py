from __future__ import annotations

import argparse
import subprocess
import json
from pathlib import Path
from typing import Any

from project_knowledge import __version__
from project_knowledge.service import ProjectService
from project_knowledge.util import hash_file, hash_text


def validate_evaluation_report(
    report_path: str | Path,
    project_root: str | Path | None = None,
    strict_live: bool = False,
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

    if payload.get("working_tree") != "clean":
        errors.append("active report must be generated from a clean working tree")

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

    warnings = payload.get("quality_gate", {}).get("warnings", [])
    warning_text = json.dumps(warnings, ensure_ascii=False).lower()
    if "adapter_unavailable" in warning_text:
        errors.append("active report must not contain adapter_unavailable")
    engine_text = json.dumps(
        codegraph.get("reproducibility", {}).get("engine", {}) if isinstance(codegraph, dict) else {},
        ensure_ascii=False,
    ).lower()
    if "builtin" in engine_text:
        errors.append("active report must not contain builtin")

    if project_root is not None:
        root = Path(project_root)
        expected_report = (root / "evaluation" / "reports" / "latest.json").resolve()
        if path.resolve() != expected_report:
            errors.append("active report path must be evaluation/reports/latest.json")
        if strict_live:
            reproducibility = payload.get("strategies", {}).get("codegraph", {}).get("reproducibility", {})
            live_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
            ).stdout.strip()
            if payload.get("project_commit") != live_head:
                errors.append("report project_commit does not match live HEAD")
            try:
                live_status = ProjectService(root).status()
            except Exception as error:
                errors.append(f"live project provenance cannot be read: {error}")
                live_status = {}
            if payload.get("index_commit") != live_status.get("index_commit"):
                errors.append("report index_commit does not match live CodeGraph index")
            dataset_name = payload.get("dataset") or reproducibility.get("dataset")
            if dataset_name:
                dataset_path = root / "evaluation" / str(dataset_name)
                if dataset_path.exists():
                    live_dataset_hash = hash_file(dataset_path)
                    if payload.get("dataset_sha256") != live_dataset_hash:
                        errors.append("report dataset_sha256 does not match live dataset")
                else:
                    errors.append(f"report dataset is missing: {dataset_path}")
            try:
                snapshot = ProjectService(root).engine.snapshot(root, ProjectService(root).config)
                live_source_hash = hash_text("\n".join(
                    f"{item.path}\t{item.content_hash}" for item in snapshot.files
                ))
                if payload.get("source_snapshot_sha256") != live_source_hash:
                    errors.append("report source_snapshot_sha256 does not match live CodeGraph snapshot")
            except Exception as error:
                errors.append(f"live CodeGraph snapshot cannot be read: {error}")

    return not errors, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate active evaluation report provenance")
    parser.add_argument("--report", default="evaluation/reports/latest.json")
    parser.add_argument("--project", default=".")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--strict-live", action="store_true", help="compare report revisions and hashes with the live checkout")
    args = parser.parse_args(argv)
    valid, errors = validate_evaluation_report(args.report, args.project, strict_live=args.strict_live)
    result = {"passed": valid, "report": str(Path(args.report)), "errors": errors}
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("PASS" if valid else "FAIL", result)
    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
