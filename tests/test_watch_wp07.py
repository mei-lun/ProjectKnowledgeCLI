from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from project_knowledge.service import ProjectService
from project_knowledge.util import ProjectLockError, sha256_bytes, watcher_lock


class WatchWP07Tests(unittest.TestCase):
    def _project(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        return temporary, root

    def test_single_watcher_coordinator_rejects_duplicate_and_recovers_dead_owner(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        ProjectService(root).initialize()
        with watcher_lock(root):
            with self.assertRaises(ProjectLockError):
                with watcher_lock(root):
                    pass
        lock_path = root / ".project-kb" / "watcher.lock"
        lock_path.write_text(json.dumps({"pid": 99999999, "created_at": time.time() - 3600}), encoding="utf-8")
        with watcher_lock(root):
            self.assertTrue(lock_path.exists())

    def test_sync_rechecks_source_hash_and_keeps_final_snapshot_consistent(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        target = root / "src" / "app.py"
        target.write_text("def value():\n    return 2\n", encoding="utf-8")
        original_parse = service.engine.parse
        changed = {"done": False}

        def racing_parse(project_root, indexed_file):
            if not changed["done"] and indexed_file.path == "src/app.py":
                changed["done"] = True
                target.write_text("def value():\n    return 3\n", encoding="utf-8")
            return original_parse(project_root, indexed_file)

        service.engine.parse = racing_parse
        result = service.sync(task_summary="验证保存竞态")
        self.assertIn("src/app.py", result["changed_files"])
        expected = sha256_bytes(target.read_bytes())
        from project_knowledge.store import KnowledgeStore
        with KnowledgeStore(service.db_path, readonly=True) as store:
            self.assertEqual(store.file_hashes()["src/app.py"], expected)

    def test_watch_once_automatically_refreshes_code_and_generated_knowledge(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        target = root / "src" / "app.py"
        target.write_text("def updated_value():\n    return 2\n", encoding="utf-8")

        service.watch(once=True)

        from project_knowledge.store import KnowledgeStore
        with KnowledgeStore(service.db_path, readonly=True) as store:
            self.assertIn("src/app.py", store.file_hashes())
            record = store.get_knowledge("generated.module.app.py")
            self.assertIsNotNone(record)
            self.assertIn("updated_value", record.content)
        self.assertEqual(service.status()["pending_files"], [])

        target.unlink()
        service.watch(once=True)
        with KnowledgeStore(service.db_path, readonly=True) as store:
            self.assertNotIn("src/app.py", store.file_hashes())
            self.assertIsNone(store.get_knowledge("generated.module.app.py"))
        self.assertEqual(service.status()["pending_files"], [])

    def test_status_detects_crashed_watcher_and_branch_transition(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        state_path = root / ".project-kb" / "state.json"
        state_path.write_text(json.dumps({"watcher": "running", "pid": 99999999}), encoding="utf-8")
        status = service.status()
        self.assertEqual(status["watcher"], "crashed")
        self.assertFalse(status["watcher_health"]["alive"])

        subprocess.run(["git", "-C", str(root), "checkout", "-qb", "feature"], check=True)
        transitioned = service.status()
        self.assertFalse(transitioned["branch_aligned"])
        self.assertFalse(service.check()[1])
        service.sync(task_summary="补偿分支切换")
        self.assertTrue(service.status()["branch_aligned"])

    def test_watch_writes_structured_log_and_install_hooks_are_owned(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        result = service.install()
        self.assertTrue(result["hooks"])
        for name in ["post-checkout", "post-merge", "pre-commit"]:
            hook = root / ".git" / "hooks" / name
            self.assertIn("project-kb:hook:start", hook.read_text(encoding="utf-8"))
        service.watch(once=True)
        state = json.loads((root / ".project-kb" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["watcher"], "stopped")
        log_path = root / ".project-kb" / "logs" / "service.jsonl"
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(event["event"] == "watch_started" for event in events))
        self.assertTrue(any(event["event"] == "watch_stopped" for event in events))


if __name__ == "__main__":
    unittest.main()
