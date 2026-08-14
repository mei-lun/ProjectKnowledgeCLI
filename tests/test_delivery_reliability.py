from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_knowledge.evaluate import evaluate_quality_gate
from project_knowledge.service import ProjectService
from scripts.validate_ci_workflow import validate_quality_workflow


class DeliveryReliabilityTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_quality_workflow_is_structurally_valid(self) -> None:
        valid, errors = validate_quality_workflow(self.ROOT / ".github" / "workflows" / "quality.yml")
        self.assertTrue(valid, errors)

    def test_failed_baseline_is_a_quality_gate_failure(self) -> None:
        report = {
            "dataset_sha256": "sha256:same",
            "strategies": {"hybrid": {"available": True, "samples": 1, "metrics": {}}},
        }
        thresholds = {"required_strategies": ["hybrid"], "minimum": {}, "maximum": {}}
        baseline = {
            "dataset_sha256": "sha256:same",
            "quality_gate": {"evaluated": True, "passed": False, "failures": [{"code": "old_failure"}]},
            "strategies": {"hybrid": {"metrics": {}}},
        }
        gate = evaluate_quality_gate(report, thresholds, baseline)
        self.assertFalse(gate["passed"])
        self.assertIn("invalid_baseline", {item["code"] for item in gate["failures"]})

    def test_suite_report_has_current_provenance(self) -> None:
        from project_knowledge.evaluate import evaluate_suite

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
            ProjectService(root).initialize()
            dataset = root / "questions.jsonl"
            dataset.write_text(json.dumps({
                "schema_version": 1,
                "id": "app",
                "task": "app",
                "category": "code",
                "expected_files": ["src/app.py"],
            }) + "\n", encoding="utf-8")
            report = evaluate_suite(root, dataset, strategies=["code"])
        self.assertTrue(report["generated_at"])
        self.assertIn("project_commit", report)
        self.assertIn("package_version", report)
        self.assertIn("working_tree", report)
        self.assertTrue(report["source_snapshot_sha256"].startswith("sha256:"))

    def test_doctor_reports_package_source_provenance(self) -> None:
        provenance = ProjectService(self.ROOT).doctor()["package_source"]
        self.assertTrue(provenance["aligned"])
        self.assertEqual(provenance["scope"], "source_checkout")
        self.assertTrue(provenance["package_file"])
        self.assertTrue(provenance["expected_source"])

        with tempfile.TemporaryDirectory() as directory:
            external = ProjectService(directory).doctor()["package_source"]
        self.assertIsNone(external["aligned"])
        self.assertEqual(external["scope"], "external_project")


if __name__ == "__main__":
    unittest.main()
