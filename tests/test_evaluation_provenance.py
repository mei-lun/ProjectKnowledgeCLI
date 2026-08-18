from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_knowledge import __version__
from scripts.validate_evaluation_provenance import validate_evaluation_report


class EvaluationProvenanceTests(unittest.TestCase):
    def _write_report(self, root: Path, payload: dict) -> Path:
        report = root / "evaluation" / "reports" / "latest.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps(payload), encoding="utf-8")
        return report

    def _valid_payload(self) -> dict:
        return {
            "package_version": __version__,
            "quality_gate": {"warnings": []},
            "strategies": {
                "codegraph": {
                    "available": True,
                    "reproducibility": {
                        "engine": {
                            "adapter": "codegraph-public-cli",
                            "available": True,
                        }
                    },
                }
            },
        }

    def test_current_codegraph_report_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._write_report(root, self._valid_payload())

            valid, errors = validate_evaluation_report(report, root)

        self.assertTrue(valid, errors)

    def test_stale_package_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self._valid_payload()
            payload["package_version"] = "0.1.28"
            report = self._write_report(root, payload)

            valid, errors = validate_evaluation_report(report, root)

        self.assertFalse(valid)
        self.assertIn("package version", " ".join(errors))

    def test_unavailable_codegraph_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self._valid_payload()
            payload["strategies"]["codegraph"]["available"] = False
            report = self._write_report(root, payload)

            valid, errors = validate_evaluation_report(report, root)

        self.assertFalse(valid)
        self.assertIn("CodeGraph strategy", " ".join(errors))

    def test_builtin_or_adapter_unavailable_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self._valid_payload()
            payload["quality_gate"]["warnings"] = [{"reason_code": "adapter_unavailable"}]
            payload["strategies"]["codegraph"]["reproducibility"]["engine"]["adapter"] = "builtin"
            report = self._write_report(root, payload)

            valid, errors = validate_evaluation_report(report, root)

        self.assertFalse(valid)
        joined = " ".join(errors)
        self.assertIn("adapter_unavailable", joined)
        self.assertIn("builtin", joined)


if __name__ == "__main__":
    unittest.main()
