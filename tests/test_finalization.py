from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from project_knowledge.cli import main
from project_knowledge.finalization import FinalizationService
from project_knowledge.service import ProjectService


def _commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True)


def _snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        if path.name in {"index.db", "index.db-wal", "index.db-shm"}:
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class FinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "tests@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Project KB Tests"], check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
        ProjectService(self.root).initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_source_commit_sync_generated_commit_becomes_ready(self) -> None:
        _commit_all(self.root, "source")

        first, first_ok = FinalizationService(self.root).finalize()

        self.assertFalse(first_ok)
        self.assertEqual(first["status"], "generated_commit_required")
        self.assertTrue(first["generated_files"])
        _commit_all(self.root, "generated")

        final, final_ok = FinalizationService(self.root).finalize(check_only=True)

        self.assertTrue(final_ok)
        self.assertEqual(final["status"], "ready")
        self.assertTrue(final["verification_aligned"])

    def test_check_only_never_writes_when_sync_is_required(self) -> None:
        _commit_all(self.root, "source")
        before = _snapshot(self.root)

        result, ok = FinalizationService(self.root).finalize(check_only=True)

        self.assertFalse(ok)
        self.assertEqual(result["status"], "sync_required")
        self.assertEqual(_snapshot(self.root), before)

    def test_non_generated_worktree_changes_require_source_commit(self) -> None:
        _commit_all(self.root, "source")
        (self.root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

        result, ok = FinalizationService(self.root).finalize()

        self.assertFalse(ok)
        self.assertEqual(result["status"], "source_commit_required")
        self.assertEqual(result["blocking_files"], ["src/app.py"])

    def test_finalize_check_cli_reports_sync_required(self) -> None:
        _commit_all(self.root, "source")
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["finalize", str(self.root), "--check", "--json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "sync_required")


if __name__ == "__main__":
    unittest.main()
