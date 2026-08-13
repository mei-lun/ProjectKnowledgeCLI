from __future__ import annotations

import unittest

from project_knowledge.retrieval import KnowledgeAPI
from tests.test_guidance_workflow import GuidanceWorkflowTests


class GuidanceRetrievalTests(GuidanceWorkflowTests):
    def test_formal_guide_is_prioritized_and_pending_body_is_not_exposed(self):
        self.confirm_catalog()
        first = self.workflow.save_draft("guidance", "run-1", self.guide(), "login")
        self.workflow.confirm_draft(first["draft_id"], first["content_hash"], "tester")
        revised = self.guide()
        revised["project_adaptation"]["steps"] = ["草稿中的秘密修订"]
        pending = self.workflow.save_draft("guidance", "run-1", revised, "login")

        api = KnowledgeAPI(self.root)
        record = api.get("guide.login")
        self.assertEqual(record["freshness"], "potentially_stale")
        self.assertEqual(record["draft_id"], pending["draft_id"])
        self.assertNotIn("草稿中的秘密修订", record["content"])

        result = api.search("登录", limit=5)["results"][0]
        self.assertEqual(result["kind"], "development-guide")
        self.assertEqual(result["freshness"], "potentially_stale")
        self.assertEqual(result["draft_id"], pending["draft_id"])
        self.assertNotIn("草稿中的秘密修订", result["summary"])

        status = api.status()
        workflow = status["guidance_workflow"]
        self.assertEqual(workflow["run"]["run_id"], "run-1")
        self.assertEqual(workflow["coverage"]["covered_files"], 1)
        self.assertEqual(workflow["coverage"]["total_files"], 1)
        self.assertEqual(workflow["pending_drafts"][0]["draft_id"], pending["draft_id"])
        self.assertEqual(workflow["pending_drafts"][0]["path"], pending["path"])

        context = api.context("扩展登录模块", max_tokens=1200)
        self.assertEqual(context["guidance_workflow"]["pending_drafts"][0]["draft_id"], pending["draft_id"])
        self.assertNotIn("草稿中的秘密修订", str(context["guidance_workflow"]))


if __name__ == "__main__":
    unittest.main()
