from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_knowledge import __version__
from scripts.validate_evaluation_provenance import (
    _is_commit_between_source_and_head,
    _report_commit_matches_live,
    validate_evaluation_report,
)


class EvaluationProvenanceTests(unittest.TestCase):
    def _write_report(self, root: Path, payload: dict) -> Path:
        report = root / "evaluation" / "reports" / "latest.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps(payload), encoding="utf-8")
        return report

    def _valid_payload(self) -> dict:
        return {
            "package_version": __version__,
            "working_tree": "clean",
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

    def test_repository_active_report_matches_current_release(self) -> None:
        root = Path(__file__).resolve().parents[1]

        valid, errors = validate_evaluation_report(
            root / "evaluation" / "reports" / "latest.json",
            root,
        )

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

    def test_dirty_worktree_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self._valid_payload()
            payload["working_tree"] = "dirty"
            report = self._write_report(root, payload)

            valid, errors = validate_evaluation_report(report, root)

        self.assertFalse(valid)
        self.assertIn("clean working tree", " ".join(errors))

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

    def test_strict_live_mode_rejects_missing_revision_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._write_report(root, self._valid_payload())

            valid, errors = validate_evaluation_report(report, root, strict_live=True)

        self.assertFalse(valid)
        self.assertTrue(any("project_commit" in error or "index_commit" in error for error in errors))

    def test_report_commit_accepts_generated_outputs_only_boundary(self) -> None:
        status = {
            "commit_alignment": "generated_outputs_only",
            "index_commit": "source-commit",
        }

        self.assertTrue(
            _report_commit_matches_live(
                "source-commit", "generated-commit", status
            )
        )
        self.assertFalse(
            _report_commit_matches_live("older-commit", "generated-commit", status)
        )

    def test_generated_report_commit_must_be_between_source_and_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess = __import__("subprocess")
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=root, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=root, check=True
            )
            commits = []
            for index in range(3):
                (root / "state.txt").write_text(str(index), encoding="utf-8")
                subprocess.run(["git", "add", "state.txt"], cwd=root, check=True)
                subprocess.run(
                    ["git", "commit", "-m", f"commit {index}"],
                    cwd=root, check=True, capture_output=True,
                )
                commits.append(
                    subprocess.run(
                        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                        capture_output=True, text=True,
                    ).stdout.strip()
                )

            self.assertTrue(
                _is_commit_between_source_and_head(
                    root, commits[1], commits[0], commits[2]
                )
            )
            self.assertFalse(
                _is_commit_between_source_and_head(
                    root, commits[0], commits[1], commits[2]
                )
            )


if __name__ == "__main__":
    unittest.main()
