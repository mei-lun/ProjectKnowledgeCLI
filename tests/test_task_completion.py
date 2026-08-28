from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_knowledge.guidance_models import GuidanceCategory, GuidanceRun
from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.store import KnowledgeStore
from project_knowledge.task_workflow import TaskCompletionWorkflow
from project_knowledge.util import utc_now


class TaskCompletionWorkflowTests(unittest.TestCase):
    def test_complete_is_idempotent_and_finds_affected_category(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".project-kb.yml").write_text("project_name: test\n", encoding="utf-8")
            db = root / ".project-kb" / "index.db"
            now = utc_now()
            with KnowledgeStore(db) as store:
                store.initialize()
                guidance = GuidanceStore(store)
                with store.transaction():
                    guidance.create_run(GuidanceRun("run-1", str(root), "snap-1", "complete", 1, 1, now, now))
                    guidance.save_category(GuidanceCategory(
                        "login", "run-1", "登录", "登录功能", [], [], ["src/login.py"],
                        [{"path": "src/login.py", "hash": "h1"}], 0.9, [], now, now,
                    ))
            fake_snapshot = {"snapshot_id": "snap-2", "files": [{"path": "src/login.py", "content_hash": "h2", "language": "Python"}]}
            with patch("project_knowledge.task_workflow.CodeGraphClient") as client:
                client.return_value.snapshot.return_value = fake_snapshot
                workflow = TaskCompletionWorkflow(root)
                first = workflow.complete("task-1", "完成登录", changed_files=["src/login.py"])
                second = workflow.complete("task-1", "重复提交", changed_files=["src/login.py"])
            self.assertEqual(first["affected_categories"], ["login"])
            self.assertEqual(first["next_action"], "generate_guidance_draft")
            self.assertEqual(second["summary"], "完成登录")
            self.assertEqual(second["task_id"], "task-1")

    def test_register_pending_requires_later_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".project-kb.yml").write_text("project_name: test\n", encoding="utf-8")
            with KnowledgeStore(root / ".project-kb" / "index.db") as store:
                store.initialize()
            with patch("project_knowledge.task_workflow.CodeGraphClient") as client:
                client.return_value.snapshot.return_value = {"snapshot_id": "snap-1", "files": []}
                result = TaskCompletionWorkflow(root).register_pending("hook-1", "任务结束")
            self.assertEqual(result["next_action"], "confirm_task_completion")
            self.assertFalse(result["user_confirmed"])


if __name__ == "__main__":
    unittest.main()
