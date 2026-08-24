from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
            self.assertIn("由 CodeGraph 在查询时实时提供", record.content)
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
        for name in ["post-checkout", "post-merge", "post-rewrite", "post-commit"]:
            hook = root / ".git" / "hooks" / name
            hook_text = hook.read_text(encoding="utf-8")
            self.assertTrue(hook_text.startswith("#!/bin/sh\n"))
            self.assertIn("project-kb:hook:start", hook_text)
            self.assertIn(f'--event "{name}"', hook_text)
        service.watch(once=True)
        state = json.loads((root / ".project-kb" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["watcher"], "stopped")
        log_path = root / ".project-kb" / "logs" / "service.jsonl"
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(event["event"] == "watch_started" for event in events))
        self.assertTrue(any(event["event"] == "watch_stopped" for event in events))

    def test_git_event_compensates_and_records_structured_state(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Project KB Tests"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)

        result = service.git_event("post-checkout", flag="1")

        self.assertEqual(result["event"], "post-checkout")
        self.assertEqual(result["state"], "branch_changed")
        self.assertFalse(result["reconciliation_required"])
        self.assertEqual(service.status()["last_git_event"]["event"], "git_event_completed")

    def test_install_preserves_user_hook_content_and_uninstall_removes_only_owned_block(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        hook = root / ".git" / "post-merge"
        hook.write_text("#!/bin/sh\nuser-hook\n", encoding="utf-8")
        service.install()
        self.assertIn("user-hook", hook.read_text(encoding="utf-8"))
        service.uninstall()
        self.assertIn("user-hook", hook.read_text(encoding="utf-8"))
        self.assertNotIn("project-kb:hook:start", hook.read_text(encoding="utf-8"))

    def test_install_uses_shared_git_hooks_directory_from_linked_worktree(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        (root / ".project-kb.yml").write_text("version: 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Project KB Tests"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
        worktree = root.parent / f"{root.name}-linked"
        self.addCleanup(lambda: subprocess.run(
            ["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)],
            check=False, capture_output=True,
        ))
        subprocess.run(["git", "-C", str(root), "worktree", "add", "-qb", "linked", str(worktree)], check=True)

        result = ProjectService(worktree).install()

        shared_hooks = Path(subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--git-path", "hooks"],
            check=True, capture_output=True, text=True,
        ).stdout.strip())
        self.assertTrue(shared_hooks.is_absolute())
        self.assertEqual(set(result["hooks"]), {
            str(shared_hooks / name)
            for name in ["post-checkout", "post-merge", "post-rewrite", "post-commit"]
        })

    def test_failed_git_event_remains_visible_as_reconciliation_required(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()

        with patch.object(service, "sync", side_effect=RuntimeError("sync failed")):
            with self.assertRaisesRegex(RuntimeError, "sync failed"):
                service.git_event("post-merge")

        status = service.status()
        self.assertTrue(status["reconciliation_required"])
        self.assertEqual(status["last_git_event"]["event"], "git_event_failed")

    def test_checkout_and_rewrite_events_realign_real_git_transitions(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Project KB Tests"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
        base = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(root), "checkout", "-qb", "feature"], check=True)

        checkout = service.git_event("post-checkout", old_head=base, new_head=base, flag="1")
        self.assertEqual(checkout["state"], "branch_changed")
        self.assertTrue(checkout["status"]["branch_aligned"])

        (root / "src" / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "src/app.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "rewrite target"], check=True)
        rewritten = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        rewrite = service.git_event("post-rewrite", old_head=base, new_head=rewritten)
        self.assertEqual(rewrite["action"], "rebuild")
        self.assertEqual(rewrite["state"], "rewritten")
        self.assertTrue(rewrite["status"]["commit_aligned"])

        subprocess.run(["git", "-C", str(root), "reset", "--hard", "-q", base], check=True)
        reset = service.git_event("post-checkout", old_head=rewritten, new_head=base, flag="1")
        self.assertEqual(reset["action"], "rebuild")
        self.assertTrue(reset["status"]["commit_aligned"])

        subprocess.run(["git", "-C", str(root), "checkout", "--detach", "-q", "HEAD"], check=True)
        detached = service.git_event("post-checkout", old_head=base, new_head=base, flag="1")
        self.assertEqual(detached["state"], "detached", detached["status"])
        self.assertEqual(detached["status"]["git_state"], "detached")
        self.assertTrue(detached["status"]["branch_aligned"])

    def test_merge_event_realigns_a_real_merge_commit(self) -> None:
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Project KB Tests"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "baseline"], check=True)
        default_branch = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(root), "checkout", "-qb", "feature"], check=True)
        (root / "src" / "feature.py").write_text("FEATURE = True\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "src/feature.py"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "feature"], check=True)
        subprocess.run(["git", "-C", str(root), "checkout", "-q", default_branch], check=True)
        old_head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-qm", "merge feature", "feature"], check=True)
        new_head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        merged = service.git_event("post-merge", old_head=old_head, new_head=new_head)

        self.assertEqual(merged["state"], "merged")
        self.assertEqual(merged["action"], "sync")
        self.assertTrue(merged["status"]["verification_aligned"])


if __name__ == "__main__":
    unittest.main()
