from __future__ import annotations

import io
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from project_knowledge.mcp import MCPServer
from project_knowledge.observability import (
    AuditIntegrityError,
    evaluate_audit_analysis,
    export_audit_log,
    validate_audit_log,
)
from project_knowledge.service import ProjectService


APP = """\
class Repository:
    def save(self, value):
        return value


def create_item(value):
    return Repository().save(value)
"""

TEST = """\
from src.app import create_item


def test_create():
    assert create_item("x") == "x"
"""


def _write_project(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "app.py").write_text(APP, encoding="utf-8")
    (root / "tests" / "test_app.py").write_text(TEST, encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='mcp-observability-sample'\n", encoding="utf-8")


def _messages() -> str:
    requests = [
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "observability-validation", "version": "1"},
            },
        },
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "knowledge_context",
                "arguments": {"task": "change Repository save behavior", "maxTokens": 1600, "debug": True},
            },
        },
        {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "knowledge_impact",
                "arguments": {"files": ["src/app.py"], "maxHops": 2, "debug": True},
            },
        },
    ]
    return "".join(json.dumps(request, ensure_ascii=False) + "\n" for request in requests)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _matches(record: dict[str, Any], label: dict[str, Any]) -> bool:
    if record.get("tool") != label.get("tool"):
        return False
    arguments = record.get("arguments", {})
    return all(arguments.get(key) == value for key, value in label.get("argument_match", {}).items())


def _bound_labels(analysis: Path, template: Path, output: Path) -> int:
    records = _read_jsonl(analysis)
    templates = _read_jsonl(template)
    bound: list[dict[str, Any]] = []
    for label in templates:
        matches = [record for record in records if _matches(record, label)]
        if len(matches) != 1:
            raise AssertionError(f"{label['case_id']}: expected one analysis match, got {len(matches)}")
        bound.append({
            key: value for key, value in label.items()
            if key not in {"case_id", "tool", "argument_match"}
        } | {
            "case_id": label["case_id"],
            "ground_truth_ref": matches[0]["ground_truth_ref"],
        })
    output.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in bound),
        encoding="utf-8",
    )
    return len(bound)


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    template = repository / "evaluation" / "mcp-observability-ground-truth.jsonl"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_project(root)
        ProjectService(root).initialize()

        stdout = io.StringIO()
        MCPServer(root, io.StringIO(_messages()), stdout).serve()
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        if len(responses) != 3:
            raise AssertionError(f"expected 3 MCP responses, got {len(responses)}")
        tool_responses = responses[1:]
        if any(response.get("result", {}).get("isError") for response in tool_responses):
            raise AssertionError(f"MCP validation call failed: {tool_responses}")

        raw_log = root / ".project-kb" / "logs" / "mcp-events.jsonl"
        validation = validate_audit_log(raw_log)
        if not validation["valid"]:
            raise AssertionError(validation)
        analysis = root / "analysis.jsonl"
        export_report = export_audit_log(raw_log, analysis)
        labels = root / "labels.jsonl"
        label_count = _bound_labels(analysis, template, labels)
        quality = evaluate_audit_analysis(analysis, labels)
        if quality["evaluated_count"] != label_count:
            raise AssertionError(quality)
        if quality["metrics"]["file_recall"] <= 0:
            raise AssertionError(f"real MCP sample returned no expected files: {quality}")

        broken_log = root / "broken-events.jsonl"
        events = raw_log.read_text(encoding="utf-8").splitlines()
        removed = False
        kept: list[str] = []
        for line in events:
            event = json.loads(line)
            if not removed and event.get("event") == "invocation_completed":
                removed = True
                continue
            kept.append(line)
        broken_log.write_text("\n".join(kept) + "\n", encoding="utf-8")
        broken_validation = validate_audit_log(broken_log)
        if broken_validation["valid"]:
            raise AssertionError("broken audit log unexpectedly validated")
        try:
            export_audit_log(broken_log, root / "must-not-exist.jsonl")
        except AuditIntegrityError:
            pass
        else:
            raise AssertionError("broken audit log unexpectedly exported")

        report = {
            "schema_version": 1,
            "validation": validation,
            "export": export_report,
            "quality": quality,
            "broken_log_issue_codes": sorted({
                issue["code"] for issue in broken_validation["issues"]
            }),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
