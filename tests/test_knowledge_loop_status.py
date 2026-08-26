from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from project_knowledge.guidance_models import GuidanceRun
from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.store import KnowledgeStore
from project_knowledge.util import utc_now


class KnowledgeLoopStatusTests(unittest.TestCase):
    def test_workflow_state_has_one_next_action_for_each_blocker(self):
        state = KnowledgeAPI._workflow_state

        self.assertEqual(
            state(None, [], [], [], [], False),
            {"state": "not_started", "next_action": "start_initialization"},
        )
        failed = SimpleNamespace(status="failed", snapshot_id="snap-1")
        self.assertEqual(
            state(failed, [], [], [], [], False),
            {"state": "failed", "next_action": "restart_initialization"},
        )
        scanning = SimpleNamespace(status="scanning", snapshot_id="snap-1")
        pending = SimpleNamespace(status="pending")
        self.assertEqual(
            state(scanning, [pending], [], [], [], False),
            {"state": "scanning", "next_action": "analyze_next_batch"},
        )
        draft = SimpleNamespace(kind="category_catalog", status="awaiting_confirmation")
        self.assertEqual(
            state(SimpleNamespace(status="category_review", snapshot_id="snap-1"), [], [draft], [], [], False),
            {"state": "draft_generation", "next_action": "draft_available"},
        )
        change = SimpleNamespace()
        self.assertEqual(
            state(SimpleNamespace(status="complete", snapshot_id="snap-1"), [], [], [change], [], False),
            {"state": "incremental", "next_action": "inspect_changes"},
        )
        self.assertEqual(
            state(SimpleNamespace(status="complete", snapshot_id="snap-1"), [], [], [], [], False),
            {"state": "ready", "next_action": "none"},
        )

    def test_confirmed_asset_counts_as_generated(self):
        category = SimpleNamespace(category_id="login")
        guidance = SimpleNamespace(
            kind="guidance", category_id="login", status="awaiting_confirmation"
        )
        result = KnowledgeAPI._workflow_state(
            SimpleNamespace(status="guidance_review", snapshot_id="snap-1"),
            [],
            [guidance],
            [],
            [category],
            False,
            {("login", "methodology")},
        )
        self.assertEqual(result["next_action"], "draft_available")

    def test_status_compares_run_baseline_to_current_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "index.db"
            with KnowledgeStore(db) as store:
                store.initialize()
                now = utc_now()
                with store.transaction():
                    GuidanceStore(store).create_run(
                        GuidanceRun(
                            "run-1", directory, "snap-old", "complete", 1, 1, now, now
                        )
                    )
                workflow = KnowledgeAPI._guidance_workflow_status(
                    store, current_snapshot_id="snap-new"
                )
        self.assertEqual(workflow["state"], "incremental")
        self.assertEqual(workflow["next_action"], "inspect_changes")

    def test_context_draft_gate_is_task_scoped(self):
        selected = [{"id": "draft.login", "ownership": "draft", "path": "docs/login.md"}]
        review = KnowledgeAPI._draft_review_gate("implement login", selected, [])
        self.assertTrue(review["review_required"])
        self.assertEqual(review["drafts"][0]["id"], "draft.login")

        no_match = KnowledgeAPI._draft_review_gate("unrelated deployment", [], [])
        self.assertFalse(no_match["review_required"])
        self.assertEqual(no_match["drafts"], [])

    def test_chinese_task_matches_pending_only_draft(self):
        pending = [{
            "draft_id": "draft-login",
            "path": ".project-kb/登录模块-待审核.md",
            "category_id": "login",
            "status": "awaiting_confirmation",
        }]
        result = KnowledgeAPI._draft_review_gate("实现登录功能", [], pending)
        self.assertTrue(result["review_required"])
        self.assertEqual(result["drafts"][0]["draft_id"], "draft-login")


if __name__ == "__main__":
    unittest.main()
