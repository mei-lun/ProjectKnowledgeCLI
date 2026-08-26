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
from project_knowledge.guidance_models import GuidanceCategory, GuidanceRun, GuidanceVersion
from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.models import KnowledgeRecord, SourceReference
from project_knowledge.service import ProjectService
from project_knowledge.store import KnowledgeStore
from project_knowledge.util import hash_text, utc_now


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


def _seed_complete_guidance(root: Path) -> None:
    service = ProjectService(root)
    snapshot = service.engine.snapshot(root, service.config)
    file_hash = next(item.content_hash for item in snapshot.files if item.path == "src/app.py")
    now = utc_now()
    evidence = [{"path": "src/app.py", "hash": file_hash}]
    methodology_body = "# methodology\n"
    guide_body = "# guide\n"
    with KnowledgeStore(service.db_path) as store:
        store.initialize()
        guidance = GuidanceStore(store)
        with store.transaction():
            guidance.create_run(GuidanceRun(
                "run-finalize", str(root.resolve()), snapshot.snapshot_id,
                "complete", len(snapshot.files), len(snapshot.files), now, now,
            ))
            guidance.save_category(GuidanceCategory(
                "app", "run-finalize", "Application", "Application behavior",
                ["src"], [], ["src/app.py"], evidence, 1.0, [], now, now,
            ))
            guidance.save_version(GuidanceVersion(
                "methodology-app-v1", "app", 1, "Application methodology",
                methodology_body, hash_text(methodology_body), snapshot.snapshot_id,
                evidence, True, now, asset_type="methodology",
            ))
            guidance.save_version(GuidanceVersion(
                "guide-app-v1", "app", 1, "Application guide", guide_body,
                hash_text(guide_body), snapshot.snapshot_id, evidence, True, now,
            ))
            for record_id, kind, title, path, body in (
                ("methodology.app", "development-methodology", "Application methodology", ".project-kb/generated/app-methodology.md", methodology_body),
                ("guide.app", "development-guide", "Application guide", ".project-kb/generated/app-guide.md", guide_body),
            ):
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(body, encoding="utf-8")
                store.upsert_knowledge(KnowledgeRecord(
                    record_id, kind, title, path, "curated", "verified",
                    sources=[SourceReference(type="file", path="src/app.py", hash=file_hash)],
                    source_hashes={"src/app.py": file_hash}, content=body,
                ))
            store.set_meta("guidance_snapshot", json.dumps({
                "snapshot_id": snapshot.snapshot_id,
                "files": {item.path: item.content_hash for item in snapshot.files},
            }, sort_keys=True))
    service.sync(task_summary="seed complete guidance for finalization test")


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
        _seed_complete_guidance(self.root)
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

    def test_configured_knowledge_index_is_a_generated_output(self) -> None:
        config_path = self.root / ".project-kb.yml"
        config = config_path.read_text(encoding="utf-8")
        config = config.replace("root: .project-kb", "root: docs/knowledge", 1)
        config_path.write_text(config, encoding="utf-8")

        service = ProjectService(self.root)

        self.assertTrue(service._is_generated_output("docs/knowledge/index.md"))
        self.assertFalse(service._is_generated_output("docs/knowledge/curated/architecture.md"))

    def test_finalize_check_cli_reports_sync_required(self) -> None:
        _commit_all(self.root, "source")
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["finalize", str(self.root), "--check", "--json"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "sync_required")

    def test_finalize_blocks_without_guidance_baseline(self) -> None:
        _commit_all(self.root, "source")
        first, _ = FinalizationService(self.root).finalize()
        self.assertEqual(first["status"], "generated_commit_required")
        _commit_all(self.root, "generated")
        result, ok = FinalizationService(self.root).finalize(check_only=True)
        self.assertFalse(ok)
        self.assertEqual(result["status"], "knowledge_review_required")
        self.assertEqual(result["gate_blockers"][0]["code"], "guidance_initialization_required")


if __name__ == "__main__":
    unittest.main()
