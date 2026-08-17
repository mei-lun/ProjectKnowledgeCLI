from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_knowledge.guidance_models import GuidanceBatch, GuidanceRun
from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.guidance_workflow import GuidanceWorkflow
from project_knowledge.config import ProjectConfig
from project_knowledge.engine import create_engine
from project_knowledge.store import KnowledgeStore
from project_knowledge.util import utc_now


class FakeClient:
    def __init__(self, snapshot):
        self.value = snapshot

    def snapshot(self):
        return self.value


class GuidanceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "login.lua").write_text(
            "local M = {}\nfunction M.login() return true end\nreturn M\n",
            encoding="utf-8",
        )
        config = ProjectConfig(project_name="guidance-fixture")
        config.write(self.root)
        create_engine(config).initialize(self.root, config)
        self.snapshot = {
            "snapshot_id": "snap-1",
            "files": [{"path": "src/login.lua", "language": "lua", "content_hash": "sha256:h", "module": "src", "symbols": []}],
        }
        self.client = FakeClient(self.snapshot)
        self.db = self.root / ".project-kb" / "index.db"
        with KnowledgeStore(self.db) as store:
            store.initialize()
            now = utc_now()
            guidance = GuidanceStore(store)
            with store.transaction():
                guidance.create_run(GuidanceRun("run-1", str(self.root.resolve()), "snap-1", "scanning", 1, 0, now, now))
                guidance.save_batch(GuidanceBatch("batch-1", "run-1", 0, "completed", ["src/login.lua"], "snap-1", now, now, result={"candidates": []}))
                run = guidance.get_run("run-1")
                run.covered_files = 1
                run.status = "category_review"
                guidance.create_run(run)
        self.workflow = GuidanceWorkflow(self.root, client=self.client)

    def tearDown(self):
        self.temp.cleanup()

    @property
    def catalog(self):
        return {"categories": [{
            "category_id": "login", "name": "登录模块", "purpose": "实现身份建立流程",
            "applies_to": ["登录类功能"], "excludes": ["活动"],
            "samples": ["src/login.lua"], "evidence": [{"path": "src/login.lua", "hash": "sha256:h"}],
            "confidence": .9, "unknowns": [], "relations": [],
        }]}

    def guide(self):
        return {
            "basic": {"title": "登录类功能开发指导"},
            "methodology_ref": {"id": "methodology.login", "title": "登录类功能轻量方法论"},
            "project_adaptation": {
                "entrypoints": ["src/login.lua"], "locations": ["src"], "call_flow": ["请求到会话"],
                "registration": ["注册协议"], "data_and_config": ["账号配置"], "steps": ["扩展处理器"],
                "invariants": ["保持会话唯一"], "testing": ["运行登录测试"], "release": ["灰度"],
                "rollback": ["恢复旧处理器"],
            },
            "variants": [], "evidence": [{"path": "src/login.lua", "hash": "sha256:h"}], "unknowns": [],
        }

    def methodology(self):
        return {
            "basic": {"title": "登录类功能轻量方法论"},
            "scope": ["用于开始身份建立类功能的首次设计对齐"],
            "questions": ["谁拥有身份事实？", "成功和失败分别如何观察？"],
            "starter_checks": ["明确入口和状态所有者", "明确失败与重复请求行为"],
            "unknowns": ["具体状态机由熟悉业务的用户在二次对齐后补充"],
        }

    def confirm_catalog(self):
        draft = self.workflow.save_draft("category_catalog", "run-1", self.catalog)
        return self.workflow.confirm_draft(draft["draft_id"], draft["content_hash"], "tester")

    def test_category_catalog_requires_visible_markdown_and_hash(self):
        draft = self.workflow.save_draft("category_catalog", "run-1", self.catalog)
        self.assertEqual(Path(draft["path"]), (self.root / ".project-kb" / "功能分类目录-待审核.md").resolve())
        self.assertTrue(Path(draft["path"]).is_file())
        Path(draft["path"]).write_text(Path(draft["path"]).read_text(encoding="utf-8") + "\n用户修改\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "哈希"):
            self.workflow.confirm_draft(draft["draft_id"], draft["content_hash"], "tester")

    def test_catalog_confirmation_and_exact_reviewed_guide(self):
        catalog = self.confirm_catalog()
        self.assertTrue(Path(catalog["path"]).is_file())
        self.assertFalse((self.root / ".project-kb" / "功能分类目录-待审核.md").exists())
        draft = self.workflow.save_draft("guidance", "run-1", self.guide(), "login")
        document = Path(draft["path"]).read_text(encoding="utf-8")
        reviewed_body = self.workflow._body_from_document(document)
        result = self.workflow.confirm_draft(draft["draft_id"], draft["content_hash"], "tester")
        self.assertEqual(Path(result["path"]).read_text(encoding="utf-8"), reviewed_body)
        self.assertFalse(Path(draft["path"]).exists())
        with KnowledgeStore(self.db) as store:
            guidance = GuidanceStore(store)
            version = guidance.current_version("login")
            self.assertEqual(version.content, reviewed_body)
            record = store.get_knowledge("guide.login")
            self.assertEqual(record.kind, "development-guide")
            self.assertEqual(record.content, reviewed_body)

    def test_methodology_and_project_guidance_are_separate_reviewable_assets(self):
        self.confirm_catalog()
        methodology = self.workflow.save_draft("methodology", "run-1", self.methodology(), "login")
        guide = self.workflow.save_draft("guidance", "run-1", self.guide(), "login")
        methodology_path = Path(methodology["path"])
        guide_path = Path(guide["path"])
        self.assertNotEqual(methodology_path, guide_path)
        self.assertIn("轻量方法论", methodology_path.read_text(encoding="utf-8"))
        guide_body = guide_path.read_text(encoding="utf-8")
        self.assertIn("方法论引用", guide_body)
        self.assertIn("当前项目事实指导", guide_body)
        self.assertNotIn("谁拥有身份事实", guide_body)

        confirmed = self.workflow.confirm_draft(
            guide["draft_id"], guide["content_hash"], "tester",
        )
        self.assertTrue(Path(confirmed["path"]).is_file())
        self.assertTrue(methodology_path.is_file())
        with KnowledgeStore(self.db) as store:
            guidance = GuidanceStore(store)
            self.assertIsNone(guidance.current_version("login", "methodology"))
            self.assertEqual(guidance.current_version("login", "project_guidance").version, 1)
            self.assertEqual(guidance.get_run("run-1").status, "guidance_review")

        method_result = self.workflow.confirm_draft(
            methodology["draft_id"], methodology["content_hash"], "tester",
        )
        with KnowledgeStore(self.db) as store:
            guidance = GuidanceStore(store)
            self.assertEqual(guidance.current_version("login", "methodology").version, 1)
            record = store.get_knowledge("methodology.login")
            self.assertEqual(record.kind, "development-methodology")
            self.assertEqual(Path(method_result["path"]).read_text(encoding="utf-8"), record.content)
            self.assertEqual(guidance.get_run("run-1").status, "complete")

    def test_incomplete_guide_cannot_be_confirmed(self):
        self.confirm_catalog()
        content = self.guide()
        content["project_adaptation"]["rollback"] = []
        draft = self.workflow.save_draft("guidance", "run-1", content, "login")
        self.assertEqual(draft["status"], "incomplete")
        with self.assertRaisesRegex(ValueError, "不完整"):
            self.workflow.confirm_draft(draft["draft_id"], draft["content_hash"], "tester")

    def test_lightweight_methodology_rejects_project_leakage_and_guide_rejects_embedded_layer(self):
        self.confirm_catalog()
        leaked = self.methodology()
        leaked["starter_checks"] = ["修改 src/login.lua", "注册 avatar_def"]
        draft = self.workflow.save_draft("methodology", "run-1", leaked, "login")
        self.assertEqual(draft["status"], "incomplete")

        mixed = self.guide()
        mixed["methodology"] = self.methodology()
        draft = self.workflow.save_draft("guidance", "run-1", mixed, "login")
        self.assertEqual(draft["status"], "incomplete")

    def test_reject_keeps_current_version(self):
        self.confirm_catalog()
        first = self.workflow.save_draft("guidance", "run-1", self.guide(), "login")
        self.workflow.confirm_draft(first["draft_id"], first["content_hash"], "tester")
        revised = self.guide()
        revised["project_adaptation"]["steps"].append("修订项目实施顺序")
        second = self.workflow.save_draft("guidance", "run-1", revised, "login")
        self.workflow.reject_draft(second["draft_id"], "tester", "需要补充")
        with KnowledgeStore(self.db) as store:
            self.assertEqual(GuidanceStore(store).current_version("login").version, 1)


if __name__ == "__main__":
    unittest.main()
