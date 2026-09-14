from __future__ import annotations

import json
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

    def add_other_category(self):
        now = utc_now()
        with KnowledgeStore(self.db) as store:
            guidance = GuidanceStore(store)
            with store.transaction():
                guidance.save_category(GuidanceCategory(
                    "other", "run-base", "Other", "Other behavior", ["other"], ["login"],
                    ["src/other.lua"], [{"path": "src/other.lua", "hash": "same"}], .9, [], now, now,
                ))
        self.client.files[1]["content_hash"] = "other-new"

    def test_generation_completes_before_review_and_baseline_waits_for_all_reviews(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        self.add_other_category()
        change = self.workflow.changes()
        first = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        self.assertEqual(self.workflow.plan(change["change_id"])["arguments"]["categoryId"], "other")
        content = self.guide_content()
        content["evidence"] = [{"path": "src/other.lua", "hash": "other-new"}]
        second = self.workflow.submit_update(change["change_id"], "guidance", category_id="other", content=content)
        self.assertTrue(self.workflow.plan(change["change_id"])["generation_complete"])
        self.assertEqual(self.workflow.changes()["status"], "pending")
        reviewer = GuidanceWorkflow(self.root, client=self.client)
        reviewer.confirm_draft(first["draft_id"], first["content_hash"], "tester")
        with KnowledgeStore(self.db) as store:
            self.assertEqual(json.loads(store.get_meta("guidance_snapshot"))["snapshot_id"], "snap-base")
        reviewer.confirm_draft(second["draft_id"], second["content_hash"], "tester")
        self.assertEqual(self.workflow.changes()["status"], "current")

    def test_rejection_reopens_generation_and_resubmission_preserves_other_draft(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        change = self.workflow.changes()
        first = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        GuidanceWorkflow(self.root, client=self.client).reject_draft(first["draft_id"], "tester", "revise")
        self.assertFalse(self.workflow.plan(change["change_id"])["generation_complete"])
        content = self.guide_content()
        content["unknowns"] = ["Revised assumption"]
        second = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=content)
        self.assertNotEqual(first["path"], second["path"])
        self.assertTrue(Path(first["path"]).exists())

    def test_new_file_returns_classification_and_can_generate_before_catalog_review(self):
        self.client.files[0]["content_hash"] = "h1"
        self.client.files.append({"path": "src/new.lua", "language": "lua", "content_hash": "new", "symbols": []})
        change = self.workflow.changes()
        plan = self.workflow.plan(change["change_id"])
        self.assertEqual(plan["arguments"]["level"], "category")
        content = {"categories": [{
            "category_id": "new", "name": "New", "purpose": "new capability",
            "applies_to": ["new"], "excludes": ["login"], "samples": ["src/new.lua"],
            "evidence": [{"path": "src/new.lua", "hash": "new"}], "confidence": .9, "unknowns": [],
        }]}
        catalog = self.workflow.submit_update(change["change_id"], "category", content=content)
        self.assertEqual(catalog["status"], "awaiting_confirmation")
        plan = self.workflow.plan(change["change_id"])
        self.assertEqual(plan["arguments"]["categoryId"], "new")
        self.assertEqual(plan["arguments"]["level"], "methodology")
        methodology = self.workflow.submit_update(change["change_id"], "methodology", category_id="new", content={
            "basic": {"title": "New approach"}, "scope": ["New capability"],
            "questions": ["What is the acceptance criterion?"], "starter_checks": ["Check expectations"],
            "unknowns": [], "evidence": [{"path": "src/new.lua", "hash": "new"}],
        })
        content = self.guide_content()
        content["evidence"] = [{"path": "src/new.lua", "hash": "new"}]
        guide = self.workflow.submit_update(change["change_id"], "guidance", category_id="new", content=content)
        self.assertTrue(self.workflow.plan(change["change_id"])["generation_complete"])
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        reviewer = GuidanceWorkflow(self.root, client=self.client)
        reviewer.confirm_draft(catalog["draft_id"], catalog["content_hash"], "tester")
        with self.assertRaisesRegex(ValueError, "methodology"):
            reviewer.confirm_draft(guide["draft_id"], guide["content_hash"], "tester")
        reviewer.confirm_draft(methodology["draft_id"], methodology["content_hash"], "tester")
        with KnowledgeStore(self.db) as store:
            self.assertEqual(json.loads(store.get_meta("guidance_snapshot"))["snapshot_id"], "snap-base")
        reviewer.confirm_draft(guide["draft_id"], guide["content_hash"], "tester")
        self.assertEqual(self.workflow.changes()["status"], "current")

    def test_deleted_file_keeps_fact_without_source_read_and_requires_manual_review(self):
        self.client.files = self.client.files[1:]
        change = self.workflow.changes()
        self.assertEqual(change["facts"], [{"path": "src/login.lua", "change": "deleted", "old_hash": "h1"}])
        self.assertEqual(self.client.source_calls, [])
        self.assertEqual(self.workflow.plan(change["change_id"])["next_action"], "manual_review")

    def test_stale_snapshot_fails_old_plan_and_new_change_can_generate(self):
        change = self.workflow.changes()
        self.client.files[0]["content_hash"] = "h3"
        with self.assertRaisesRegex(ValueError, "snapshot|快照"):
            self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        self.assertEqual(self.workflow.plan(change["change_id"])["next_action"], "retry")
        latest = self.workflow.changes()
        self.assertNotEqual(latest["change_id"], change["change_id"])
        self.assertNotEqual(latest["run_id"], change["run_id"])

    def test_public_query_supplies_symbols_when_file_list_has_none(self):
        self.client.files[0]["symbols"] = []
        self.client.query = lambda search, limit=20: [{"node": {"name": "login", "filePath": "src/login.lua"}}]
        change = self.workflow.changes()
        self.assertEqual(self.client.impact_calls, [("login", 2)])
        self.assertEqual(change["facts"][0]["analysis"]["symbol_source"], "query")

    def test_stale_review_fails_change_without_publishing(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        change = self.workflow.changes()
        draft = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        self.client.files[0]["content_hash"] = "h3"
        with self.assertRaisesRegex(ValueError, "snapshot"):
            GuidanceWorkflow(self.root, client=self.client).confirm_draft(draft["draft_id"], draft["content_hash"], "tester")
        self.assertEqual(self.workflow.plan(change["change_id"])["next_action"], "retry")
        with KnowledgeStore(self.db) as store:
            self.assertEqual(GuidanceStore(store).current_version("login").version, 1)
            self.assertEqual(json.loads(store.get_meta("guidance_snapshot"))["snapshot_id"], "snap-base")

    def test_explicit_retry_after_snapshot_returns_uses_new_run_and_preserves_draft(self):
        change = self.workflow.changes()
        first = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        self.client.files[0]["content_hash"] = "h3"
        self.workflow.plan(change["change_id"])
        self.client.files[0]["content_hash"] = "h2"
        retry = self.workflow.changes(retry=True)
        self.assertNotEqual(retry["run_id"], change["run_id"])
        second = self.workflow.submit_update(retry["change_id"], "guidance", category_id="login", content=self.guide_content())
        self.assertNotEqual(first["path"], second["path"])
        self.assertTrue(Path(first["path"]).exists())

    def test_deletion_can_use_existing_surviving_category_evidence(self):
        self.client.files = self.client.files[1:]
        with KnowledgeStore(self.db) as store:
            guidance = GuidanceStore(store)
            category = guidance.list_categories()[0]
            category.samples.append("src/other.lua")
            with store.transaction():
                guidance.save_category(category)
        change = self.workflow.changes()
        plan = self.workflow.plan(change["change_id"])
        self.assertEqual(plan["next_action"], "generate_guidance")
        self.assertEqual(plan["evidence"], [{"path": "src/other.lua", "hash": "same"}])

    def test_fact_without_evidence_cannot_advance(self):
        change = self.workflow.changes()
        with self.assertRaises(ValueError):
            self.workflow.submit_update(change["change_id"], "fact", category_id="login", content={"guidance_unchanged": True})
        self.assertEqual(self.workflow.changes()["status"], "pending")

    def test_replacement_draft_invalidates_older_review(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        change = self.workflow.changes()
        first = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        with KnowledgeStore(self.db) as store:
            draft = GuidanceStore(store).get_draft(first["draft_id"])
        content = dict(draft.payload)
        content["unknowns"] = ["Updated finding"]
        reviewer = GuidanceWorkflow(self.root, client=self.client)
        second = reviewer.save_draft("guidance", change["run_id"], content, "login")
        self.assertNotEqual(first["draft_id"], second["draft_id"])
        with self.assertRaisesRegex(ValueError, "superseded"):
            reviewer.confirm_draft(first["draft_id"], first["content_hash"], "tester")

    def test_resubmitting_old_body_becomes_latest_after_other_rejection(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        change = self.workflow.changes()
        reviewer = GuidanceWorkflow(self.root, client=self.client)
        original = self.guide_content()
        first = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=original)
        reviewer.reject_draft(first["draft_id"], "tester", "revise")
        revised = self.guide_content()
        revised["unknowns"] = ["Alternative explanation"]
        second = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=revised)
        reviewer.reject_draft(second["draft_id"], "tester", "restore original")
        restored = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=original)
        self.assertEqual(restored["draft_id"], first["draft_id"])
        plan = self.workflow.plan(change["change_id"])
        self.assertTrue(plan["generation_complete"])
        self.assertEqual(plan["items"][0]["draft_id"], restored["draft_id"])
        reviewer.confirm_draft(restored["draft_id"], restored["content_hash"], "tester")
        self.assertEqual(self.workflow.changes()["status"], "current")

    def test_revised_catalog_drops_abandoned_provisional_requirements(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        self.client.files.append({"path": "src/new.lua", "language": "lua", "content_hash": "new", "symbols": []})
        change = self.workflow.changes()
        reviewer = GuidanceWorkflow(self.root, client=self.client)

        def catalog(category_id):
            return {"categories": [{
                "category_id": category_id, "name": category_id, "purpose": "New capability",
                "applies_to": ["new"], "excludes": ["login"], "samples": ["src/new.lua"],
                "evidence": [{"path": "src/new.lua", "hash": "new"}], "confidence": .9, "unknowns": [],
            }]}

        wrong = self.workflow.submit_update(change["change_id"], "category", content=catalog("wrong"))
        self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        old_methodology = self.workflow.submit_update(change["change_id"], "methodology", category_id="wrong", content={
            "basic": {"title": "Approach"}, "scope": ["New capability"],
            "questions": ["What is expected?"], "starter_checks": ["Check expectations"], "unknowns": [],
            "evidence": [{"path": "src/new.lua", "hash": "new"}],
        })
        reviewer.reject_draft(wrong["draft_id"], "tester", "wrong category")
        replacement = self.workflow.submit_update(change["change_id"], "category", content=catalog("new"))
        plan = self.workflow.plan(change["change_id"])
        self.assertEqual([(item["kind"], item["category_id"]) for item in plan["remaining"]],
                         [("guidance", "login"), ("methodology", "new"), ("guidance", "new")])
        with KnowledgeStore(self.db) as store:
            state = GuidanceStore(store).pending_changes()[0]
            self.assertEqual(state.affected_categories, ["login", "new"])
            self.assertEqual(state.payload["methodologyCategories"], ["new"])
        reviewer.confirm_draft(replacement["draft_id"], replacement["content_hash"], "tester")
        with self.assertRaises(ValueError):
            reviewer.confirm_draft(old_methodology["draft_id"], old_methodology["content_hash"], "tester")
        with KnowledgeStore(self.db) as store:
            self.assertIsNone(GuidanceStore(store).current_version("wrong", "methodology"))

    def test_catalog_revision_preserves_confirmed_independent_guidance(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        self.add_other_category()
        change = self.workflow.changes()
        reviewer = GuidanceWorkflow(self.root, client=self.client)
        confirmed = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        reviewer.confirm_draft(confirmed["draft_id"], confirmed["content_hash"], "tester")

        def catalog(category_id):
            return {"categories": [{
                "category_id": category_id, "name": category_id, "purpose": "Other behavior",
                "applies_to": ["other"], "excludes": ["login"], "samples": ["src/other.lua"],
                "evidence": [{"path": "src/other.lua", "hash": "other-new"}], "confidence": .9, "unknowns": [],
            }]}

        wrong = self.workflow.submit_update(change["change_id"], "category", content=catalog("wrong"))
        reviewer.reject_draft(wrong["draft_id"], "tester", "wrong grouping")
        self.workflow.submit_update(change["change_id"], "category", content=catalog("new"))
        plan = self.workflow.plan(change["change_id"])
        login = next(item for item in plan["items"] if item["category_id"] == "login")
        self.assertEqual(login["status"], "processed")
        self.assertEqual(login["draft_id"], confirmed["draft_id"])
        self.assertNotIn("wrong", [item["category_id"] for item in plan["items"]])
        self.assertIn("other", [item["category_id"] for item in plan["remaining"]])
        with KnowledgeStore(self.db) as store:
            self.assertEqual(GuidanceStore(store).current_version("login").version, 2)
            self.assertEqual(json.loads(store.get_meta("guidance_snapshot"))["snapshot_id"], "snap-base")

    def test_unknown_task_association_is_rejected_and_private_input_is_not_trusted(self):
        change = self.workflow.changes()
        content = self.guide_content()
        content["_task_id"] = "forged"
        with self.assertRaisesRegex(ValueError, "task association"):
            self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=content, task_id="unknown")
        result = self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=content)
        with KnowledgeStore(self.db) as store:
            self.assertNotIn("_task_id", GuidanceStore(store).get_draft(result["draft_id"]).payload)

    def test_successful_retry_retires_failed_change(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        original = self.workflow.changes()
        self.client.files[0]["content_hash"] = "h3"
        latest = self.workflow.changes(retry=True)
        content = self.guide_content()
        content["evidence"] = [{"path": "src/login.lua", "hash": "h3"}]
        draft = self.workflow.submit_update(latest["change_id"], "guidance", category_id="login", content=content)
        GuidanceWorkflow(self.root, client=self.client).confirm_draft(draft["draft_id"], draft["content_hash"], "tester")
        with KnowledgeStore(self.db) as store:
            self.assertEqual(GuidanceStore(store).pending_changes(), [])
        self.assertEqual(self.workflow.plan(original["change_id"])["next_action"], "superseded")

    def test_rejected_catalog_reopens_dependent_generation(self):
        from project_knowledge.guidance_workflow import GuidanceWorkflow
        change = self.workflow.changes()
        catalog = self.workflow.submit_update(change["change_id"], "category", content={"categories": [{
            "category_id": "login", "name": "Login", "purpose": "session",
            "applies_to": ["login"], "excludes": ["other"], "samples": ["src/login.lua"],
            "evidence": [{"path": "src/login.lua", "hash": "h2"}], "confidence": .9, "unknowns": [],
        }]})
        self.workflow.submit_update(change["change_id"], "guidance", category_id="login", content=self.guide_content())
        GuidanceWorkflow(self.root, client=self.client).reject_draft(catalog["draft_id"], "tester", "scope wrong")
        plan = self.workflow.plan(change["change_id"])
        self.assertEqual([item["kind"] for item in plan["remaining"]], ["category", "guidance"])

    def test_unreviewed_new_category_does_not_classify_retry(self):
        self.client.files.append({"path": "src/new.lua", "language": "lua", "content_hash": "new", "symbols": []})
        change = self.workflow.changes()
        self.workflow.submit_update(change["change_id"], "category", content={"categories": [{
            "category_id": "new", "name": "New", "purpose": "new capability",
            "applies_to": ["new"], "excludes": ["login"], "samples": ["src/new.lua"],
            "evidence": [{"path": "src/new.lua", "hash": "new"}], "confidence": .9, "unknowns": [],
        }]})
        self.client.files[-1]["content_hash"] = "newer"
        latest = self.workflow.changes(retry=True)
        self.assertIn("src/new.lua", latest["unclassified_files"])
        self.assertEqual(latest["next_action"], "generate_category")

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

    def test_multi_category_progress_does_not_advance_baseline_early(self):
        self.workflow.changes()
        with KnowledgeStore(self.db) as store:
            guidance = GuidanceStore(store)
            change = guidance.pending_changes()[0]
            change.payload["pendingCategories"] = ["login", "other"]
            change.payload["completedCategories"] = []
            guidance.save_change(change)
            store.connection.commit()
            self.workflow._advance(store, guidance, change, "login")
            self.assertEqual(guidance.pending_changes()[0].payload["pendingCategories"], ["other"])
            self.assertEqual(json.loads(store.get_meta("guidance_snapshot"))["snapshot_id"], "snap-base")
            self.workflow._advance(store, guidance, guidance.pending_changes()[0], "other")
            self.assertEqual(guidance.pending_changes(), [])
            self.assertEqual(json.loads(store.get_meta("guidance_snapshot"))["snapshot_id"], self.client.snapshot()["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
