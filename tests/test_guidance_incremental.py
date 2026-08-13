from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_knowledge.guidance_models import GuidanceCategory, GuidanceRun, GuidanceVersion
from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.incremental import IncrementalWorkflow
from project_knowledge.models import KnowledgeRecord, SourceReference
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.store import KnowledgeStore
from project_knowledge.util import hash_text, utc_now


class FakeCodeGraph:
    def __init__(self):
        self.files = [
            {"path": "src/login.lua", "language": "lua", "content_hash": "h2", "module": "src", "symbols": [{"name": "login"}]},
            {"path": "src/other.lua", "language": "lua", "content_hash": "same", "module": "src", "symbols": [{"name": "other"}]},
        ]
        self.source_calls = []
        self.impact_calls = []

    def snapshot(self):
        import hashlib, json
        identity = [{"path": item["path"], "language": item["language"], "content_hash": item["content_hash"]} for item in self.files]
        return {"snapshot_id": hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "files": self.files}

    def source(self, path, start_line=1, limit=400):
        self.source_calls.append(path)
        return "function login() end"

    def impact(self, symbol, depth=2):
        self.impact_calls.append((symbol, depth))
        return {"affected": [{"filePath": "src/login.lua", "name": "login"}]}


class GuidanceIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = FakeCodeGraph()
        self.db = self.root / ".project-kb" / "index.db"
        now = utc_now()
        body = "# 登录类功能开发指导\n"
        with KnowledgeStore(self.db) as store:
            store.initialize()
            guidance = GuidanceStore(store)
            with store.transaction():
                guidance.create_run(GuidanceRun("run-base", str(self.root.resolve()), "snap-base", "complete", 2, 2, now, now))
                guidance.save_category(GuidanceCategory(
                    "login", "run-base", "登录模块", "身份建立", ["登录"], ["活动"],
                    ["src/login.lua"], [{"path": "src/login.lua", "hash": "h1"}], .9, [], now, now,
                ))
                guidance.save_version(GuidanceVersion(
                    "guide-login-v1", "login", 1, "登录模块开发指导", body, hash_text(body),
                    "snap-base", [{"path": "src/login.lua", "hash": "h1"}], True, now,
                ))
                store.upsert_knowledge(KnowledgeRecord(
                    "guide.login", "development-guide", "登录模块开发指导",
                    ".project-kb/登录模块-开发指导.md", "curated", "verified",
                    sources=[SourceReference(type="file", path="src/login.lua", hash="h1")],
                    source_hashes={"src/login.lua": "h1"}, content=body,
                ))
                store.set_meta("guidance_snapshot", '{"snapshot_id":"snap-base","files":{"src/login.lua":"h1","src/other.lua":"same"}}')
        self.workflow = IncrementalWorkflow(self.root, client=self.client)

    def tearDown(self):
        self.temp.cleanup()

    def guide_content(self):
        return {
            "basic": {"title": "登录类功能开发指导"},
            "methodology_ref": {"id": "methodology.login", "title": "登录类功能轻量方法论"},
            "project_adaptation": {
                "entrypoints": ["src/login.lua"], "locations": ["src"], "call_flow": ["请求到会话"],
                "registration": ["协议注册"], "data_and_config": ["账号配置"], "steps": ["修改处理器"],
                "invariants": ["唯一会话"], "testing": ["登录测试"], "release": ["灰度"], "rollback": ["恢复旧版本"],
            },
            "variants": [], "evidence": [{"path": "src/login.lua", "hash": "h2"}], "unknowns": [],
        }

    def test_changes_reads_only_changed_source_and_depth_two_impact(self):
        result = self.workflow.changes()
        self.assertEqual(result["modified"], ["src/login.lua"])
        self.assertEqual(self.client.source_calls, ["src/login.lua"])
        self.assertEqual(self.client.impact_calls, [("login", 2)])
        self.assertEqual(result["affected_categories"], ["login"])

    def test_fact_update_keeps_body_and_advances_baseline(self):
        change = self.workflow.changes()
        result = self.workflow.submit_update(
            change["change_id"], "fact", category_id="login",
            content={"guidance_unchanged": True},
            evidence=[{"path": "src/login.lua", "hash": "h2"}],
        )
        self.assertEqual(result["status"], "completed")
        with KnowledgeStore(self.db) as store:
            guidance = GuidanceStore(store)
            self.assertEqual(guidance.current_version("login").version, 2)
            self.assertEqual(guidance.current_version("login").content, "# 登录类功能开发指导\n")
            self.assertEqual(guidance.pending_changes(), [])
        self.assertEqual(self.workflow.changes()["status"], "current")

    def test_invalid_fact_does_not_advance(self):
        change = self.workflow.changes()
        with self.assertRaisesRegex(ValueError, "证据"):
            self.workflow.submit_update(
                change["change_id"], "fact", category_id="login",
                content={"guidance_unchanged": True},
                evidence=[{"path": "src/login.lua", "hash": "bad"}],
            )
        self.assertEqual(self.workflow.changes()["change_id"], change["change_id"])

    def test_guidance_confirmation_advances_change(self):
        change = self.workflow.changes()
        draft = self.workflow.submit_update(
            change["change_id"], "guidance", category_id="login",
            content=self.guide_content(),
        )
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        GuidanceWorkflow(self.root, client=self.client).confirm_draft(
            draft["draft_id"], draft["content_hash"], "tester",
        )
        self.assertEqual(self.workflow.changes()["status"], "current")

    def test_category_confirmation_waits_for_guidance_regeneration(self):
        change = self.workflow.changes()
        content = {"categories": [{
            "category_id": "login", "name": "登录模块", "purpose": "身份建立",
            "applies_to": ["登录"], "excludes": ["活动"], "samples": ["src/login.lua"],
            "evidence": [{"path": "src/login.lua", "hash": "h2"}],
            "confidence": .9, "unknowns": [], "relations": [],
        }]}
        draft = self.workflow.submit_update(change["change_id"], "category", content=content)
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        GuidanceWorkflow(self.root, client=self.client).confirm_draft(
            draft["draft_id"], draft["content_hash"], "tester",
        )
        with KnowledgeStore(self.db) as store:
            rows = store.rows(
                "SELECT status FROM guidance_runs WHERE snapshot_id=?",
                [change["head_snapshot_id"]],
            )
            self.assertEqual(rows[0]["status"], "guidance_generation")
            self.assertEqual(len(GuidanceStore(store).pending_changes()), 1)

    def test_guidance_update_creates_reviewable_draft(self):
        change = self.workflow.changes()
        result = self.workflow.submit_update(
            change["change_id"], "guidance", category_id="login",
            content=self.guide_content(),
        )
        self.assertEqual(result["status"], "awaiting_confirmation")
        self.assertTrue(Path(result["path"]).is_file())
        record = KnowledgeAPI(self.root).get("guide.login")
        self.assertEqual(record["freshness"], "potentially_stale")
        self.assertNotIn("扩展流程", record["content"])


if __name__ == "__main__":
    unittest.main()
