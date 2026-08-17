from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path


def validate_quality_workflow(path: str | Path) -> tuple[bool, list[str]]:
    """Validate the indentation-sensitive command block used by the quality job.

    This deliberately checks the subset that has broken in this repository. The
    GitHub Actions parser remains the final YAML validator, while this check is
    dependency-free and runs in the normal unit-test suite.
    """
    workflow = Path(path)
    errors: list[str] = []
    try:
        lines = workflow.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return False, [str(error)]

    folded_line = next((index for index, line in enumerate(lines) if line.strip() == "run: >-"), None)
    if folded_line is None:
        errors.append("quality job must use a folded run command")
        return False, errors

    run_indent = len(lines[folded_line]) - len(lines[folded_line].lstrip())
    command_lines: list[tuple[int, str]] = []
    for number, line in enumerate(lines[folded_line + 1 :], folded_line + 2):
        if line.strip().startswith("- "):
            if command_lines:
                break
            continue
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= run_indent:
            break
        command_lines.append((number, line))
    if not command_lines:
        errors.append("quality job folded command is empty")
    for number, line in command_lines:
        indent = len(line) - len(line.lstrip())
        if indent <= run_indent:
            errors.append(f"line {number}: folded command must be indented beyond run")
    if not any("--thresholds evaluation/thresholds.json" in line for _, line in command_lines):
        errors.append("quality job must pass evaluation thresholds")
    command = " ".join(line.strip() for _, line in command_lines)
    try:
        arguments = shlex.split(command)
    except ValueError as error:
        errors.append(f"quality job command cannot be parsed: {error}")
        arguments = []
    if arguments:
        if "--baseline" in arguments:
            try:
                dataset_argument = arguments[arguments.index("evaluate") + 1]
                baseline_argument = arguments[arguments.index("--baseline") + 1]
            except (ValueError, IndexError):
                errors.append("quality job baseline argument is incomplete")
            else:
                root = workflow.parents[2]
                dataset = root / dataset_argument
                baseline = root / baseline_argument
                try:
                    dataset_hash = "sha256:" + hashlib.sha256(dataset.read_bytes()).hexdigest()
                    baseline_payload = json.loads(baseline.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    errors.append(f"quality evaluation input cannot be read: {error}")
                else:
                    if baseline_payload.get("dataset_sha256") != dataset_hash:
                        errors.append("quality baseline dataset hash does not match evaluation dataset")
    if not any(line.strip() == "run: project-kb finalize . --check --json" for line in lines):
        errors.append("quality job must verify deterministic release finalization")
    return not errors, errors
