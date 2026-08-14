from __future__ import annotations

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
    if not any("--baseline evaluation/baselines/" in line for _, line in command_lines):
        errors.append("quality job must compare against a passing evaluation baseline")
    if not any(line.strip() == "run: project-kb finalize . --check --json" for line in lines):
        errors.append("quality job must verify deterministic release finalization")
    return not errors, errors
