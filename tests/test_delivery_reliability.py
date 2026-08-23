from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_knowledge.evaluate import evaluate_quality_gate
from project_knowledge.service import ProjectService
from scripts.validate_ci_workflow import validate_quality_workflow
from scripts.validate_evaluation_dataset import validate_evaluation_dataset


class DeliveryReliabilityTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_quality_workflow_is_structurally_valid(self) -> None:
        valid, errors = validate_quality_workflow(self.ROOT / ".github" / "workflows" / "quality.yml")
        self.assertTrue(valid, errors)

    def test_quality_workflow_rejects_a_baseline_for_another_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github" / "workflows" / "quality.yml"
            workflow.parent.mkdir(parents=True)
            (root / "evaluation" / "baselines").mkdir(parents=True)
            (root / "evaluation" / "questions.jsonl").write_text('{"id":"current"}\n', encoding="utf-8")
            (root / "evaluation" / "thresholds.json").write_text("{}\n", encoding="utf-8")
            (root / "evaluation" / "baselines" / "old.json").write_text(
                json.dumps({"dataset_sha256": "sha256:old"}) + "\n",
                encoding="utf-8",
            )
            workflow.write_text(
                "jobs:\n"
                "  quality:\n"
                "    steps:\n"
                "      - name: evaluate\n"
                "        run: >-\n"
                "          project-kb evaluate evaluation/questions.jsonl\n"
                "          --thresholds evaluation/thresholds.json\n"
                "          --baseline evaluation/baselines/old.json\n"
                "          --strict-live\n"
                "          --enforce-gate\n"
                "      - name: finalize\n"
                "        run: project-kb finalize . --check --json\n",
                encoding="utf-8",
            )

            valid, errors = validate_quality_workflow(workflow)

        self.assertFalse(valid)
        self.assertIn("quality baseline dataset hash does not match evaluation dataset", errors)

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

    def test_evaluation_dataset_gate_rejects_current_small_single_snapshot_seed(self) -> None:
        valid, errors, report = validate_evaluation_dataset([
            self.ROOT / "evaluation" / "questions-gardenserver-phase0.jsonl"
        ])

        self.assertFalse(valid)
        self.assertLess(report["questions"], 300)
        self.assertTrue(any("questions" in error for error in errors))

    def test_evaluation_dataset_gate_accepts_stratified_multi_snapshot_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.jsonl"
            rows = [
                {"id": f"{snapshot}-{query_type}-{index}", "query_type": query_type, "snapshot_id": snapshot}
                for snapshot in ("repo-a", "repo-b", "repo-c")
                for query_type in ("code", "impact", "invariant", "design")
                for index in range(30)
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            valid, errors, report = validate_evaluation_dataset([path])

        self.assertTrue(valid, errors)
        self.assertEqual(report["questions"], 360)

    def test_evaluation_manifest_rejects_unlocked_snapshot_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "questions.jsonl"
            dataset.write_text("\n".join(json.dumps({"query_type": "code", "snapshot_id": "a"}) for _ in range(300)) + "\n", encoding="utf-8")
            digest = "sha256:" + __import__("hashlib").sha256(dataset.read_bytes()).hexdigest()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"path": str(dataset), "sha256": digest}],
                "snapshots": [{"id": "a"}],
            }), encoding="utf-8")
            valid, errors, _ = validate_evaluation_dataset([dataset], manifest=manifest)

        self.assertFalse(valid)
        self.assertTrue(any("snapshots" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
