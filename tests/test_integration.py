from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_knowledge.guidance_models import (
    GuidanceBatch,
    GuidanceCategory,
    GuidanceChange,
    GuidanceDraft,
    GuidanceRun,
    GuidanceVersion,
)
from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.mcp import MCPServer
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.service import ProjectService
from project_knowledge.store import KnowledgeStore


APP_V1 = '''
class Repository:
    def save(self, value):
        return value

def create_item(value):
    return Repository().save(value)
'''

APP_V2 = '''
class Repository:
    def save(self, value):
        return {"saved": value}

def create_item(value):
    return Repository().save(value)
'''

APP_V3 = '''
class Repository:
    def save(self, value):
        return {"id": 1, "saved": value}

def create_item(value):
    return Repository().save(value)
'''


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "app.py").write_text(APP_V1, encoding="utf-8")
        (self.root / "tests" / "test_app.py").write_text(
            "from src.app import create_item\n\ndef test_create():\n    assert create_item('x')\n",
            encoding="utf-8",
        )
        (self.root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_init_sync_freshness_retrieval_and_mcp(self) -> None:
        service = ProjectService(self.root)
        report = service.initialize()
        self.assertEqual(report["action"], "init")
        self.assertGreaterEqual(report["symbols"], 5)
        self.assertTrue((self.root / ".project-kb" / "index.db").exists())
        self.assertTrue((self.root / ".project-kb" / "mcp.json").exists())
        project_map = self.root / ".project-kb" / "generated" / "project-map.md"
        self.assertTrue(project_map.exists())
        project_map_content = project_map.read_text(encoding="utf-8")
        self.assertIn("# 项目地图：", project_map_content)
        self.assertIn("| 配置 |", project_map_content)
        self.assertIn("# 项目知识库：", (self.root / ".project-kb" / "index.md").read_text(encoding="utf-8"))
        self.assertIn("# 架构", (self.root / ".project-kb" / "curated" / "architecture.md").read_text(encoding="utf-8"))
        module_map = self.root / ".project-kb" / "generated" / "modules" / "app.py.md"
        self.assertIn("：类，位于", module_map.read_text(encoding="utf-8"))
        self.assertEqual(service.status()["pending_files"], [])
        manifest = json.loads((self.root / ".project-kb" / "manifest.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(manifest["records"]), 6)
        self.assertNotIn(str(self.root), json.dumps(manifest))

        architecture = self.root / ".project-kb" / "curated" / "architecture.md"
        architecture.write_text(
            '# Architecture\n\nRepository owns persistence.\n\n<!-- project-kb:source file="src/app.py" -->\n',
            encoding="utf-8",
        )
        self.assertIn(".project-kb/curated/architecture.md", service.status()["pending_files"])
        first_sync = service.sync(task_summary="document repository ownership")
        self.assertIn(".project-kb/curated/architecture.md", first_sync["changed_knowledge"])
        self.assertIsNone(first_sync["semantic_update"])
        with KnowledgeStore(service.db_path, readonly=True) as store:
            curated = store.get_knowledge("curated.architecture")
            self.assertIsNotNone(curated)
            self.assertEqual(curated.status, "fresh")
            self.assertEqual(curated.confidence, "verified")

        (self.root / "src" / "app.py").write_text(APP_V2, encoding="utf-8")
        pending_api = KnowledgeAPI(self.root)
        pending_record = pending_api.get("generated.module.app.py")
        self.assertNotIn("content", pending_record)
        self.assertIn("src/app.py", pending_record["withheld"])
        source_sync = service.sync(task_summary="change repository return shape")
        self.assertRegex(source_sync["semantic_update"], r"^sq-[0-9a-f]{16}$")
        self.assertGreaterEqual(service.status()["semantic_update_queue"], 1)
        self.assertIn("Repository owns persistence", architecture.read_text(encoding="utf-8"))
        with KnowledgeStore(service.db_path, readonly=True) as store:
            curated = store.get_knowledge("curated.architecture")
            self.assertEqual(curated.status, "potentially_stale")

        service.rebuild()
        with KnowledgeStore(service.db_path, readonly=True) as store:
            self.assertEqual(store.get_knowledge("curated.architecture").status, "potentially_stale")

        architecture.write_text(architecture.read_text(encoding="utf-8") + "\nReviewed against the current source.\n", encoding="utf-8")
        service.sync(task_summary="review architecture knowledge")
        with KnowledgeStore(service.db_path, readonly=True) as store:
            self.assertEqual(store.get_knowledge("curated.architecture").status, "fresh")

        api = KnowledgeAPI(self.root)
        search = api.search("Repository persistence")
        self.assertTrue(search["results"])
        context = api.context("change Repository save behavior", max_tokens=1200)
        self.assertLessEqual(context["estimated_tokens"], 1200)
        self.assertLessEqual(len(context["knowledge"]), 4)
        self.assertIn("python -m pytest", context["verification_commands"])
        impact = api.impact(files=["src/app.py"])
        self.assertIn("app.py", impact["affected_modules"])
        self.assertIn("tests/test_app.py", impact["affected_tests"])

        server = MCPServer(self.root, io.StringIO(), io.StringIO())
        initialized = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        })
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
        tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(len(tools["result"]["tools"]), 5)
        called = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "knowledge_status", "arguments": {}},
        })
        self.assertFalse(called["result"]["isError"])

    def test_rebuild_preserves_complete_guidance_graph(self) -> None:
        service = ProjectService(self.root)
        service.initialize()
        now = "2026-08-13T09:00:00+08:00"
        run = GuidanceRun(
            run_id="run-rebuild", project_root=str(self.root), snapshot_id="snapshot-r",
            status="guidance_review", total_files=2, covered_files=2,
            created_at=now, updated_at=now,
        )
        batch = GuidanceBatch(
            batch_id="batch-rebuild", run_id=run.run_id, ordinal=0,
            status="completed", files=["src/app.py"], snapshot_id=run.snapshot_id,
            result={"category_ids": ["category-rebuild"]}, created_at=now, updated_at=now,
        )
        category = GuidanceCategory(
            category_id="category-rebuild", run_id=run.run_id, name="通用功能",
            purpose="验证重建", applies_to=["功能"], excludes=[], samples=["src/app.py"],
            evidence=[{"path": "src/app.py"}], confidence=0.9, unknowns=[],
            created_at=now, updated_at=now,
        )
        draft = GuidanceDraft(
            draft_id="draft-rebuild", run_id=run.run_id, category_id=category.category_id,
            kind="guidance", status="confirmed",
            path=str(self.root / ".project-kb" / "通用功能-开发指导.md"),
            content_hash="sha256:" + "d" * 64, snapshot_id=run.snapshot_id,
            payload={"title": "通用功能"}, created_at=now, updated_at=now,
            confirmed_at=now,
        )
        version = GuidanceVersion(
            version_id="version-rebuild", category_id=category.category_id,
            draft_id=draft.draft_id, version=1, title="通用功能开发指导", content="正文",
            content_hash="sha256:" + "e" * 64, snapshot_id=run.snapshot_id,
            evidence=[{"path": "src/app.py"}], is_current=True, created_at=now,
        )
        change = GuidanceChange(
            change_id="change-rebuild", project_root=str(self.root),
            base_snapshot_id="snapshot-old", head_snapshot_id=run.snapshot_id,
            update_level="guidance", changed_files=["src/app.py"],
            affected_categories=[category.category_id], payload={"handled": True},
            created_at=now, processed_at=now,
        )
        with KnowledgeStore(service.db_path) as store:
            guidance = GuidanceStore(store)
            with store.transaction():
                guidance.create_run(run)
                guidance.save_batch(batch)
                guidance.save_category(category)
                guidance.save_draft(draft)
                guidance.save_version(version)
                guidance.save_change(change)
            before = store.export_guidance_graph()

        service.rebuild()

        with KnowledgeStore(service.db_path, readonly=True) as store:
            after = store.export_guidance_graph()
            self.assertEqual(after, before)
            current = GuidanceStore(store).current_version(category.category_id)
            self.assertIsNotNone(current)
            self.assertEqual(current.version_id, version.version_id)
            self.assertTrue(current.is_current)

    def test_dry_run_and_deleted_file_sync(self) -> None:
        service = ProjectService(self.root)
        dry_run = service.initialize(dry_run=True)
        self.assertFalse((self.root / ".project-kb.yml").exists())
        self.assertGreater(dry_run["files_to_index"], 0)
        service.initialize()
        (self.root / "tests" / "test_app.py").unlink()
        preview = service.sync(dry_run=True)
        self.assertEqual(preview["deleted_files"], ["tests/test_app.py"])
        service.sync()
        with KnowledgeStore(service.db_path, readonly=True) as store:
            self.assertNotIn("tests/test_app.py", store.file_hashes())

    def test_install_and_uninstall_only_remove_owned_integration(self) -> None:
        service = ProjectService(self.root)
        service.initialize()
        service.install()
        agents = self.root / "AGENTS.md"
        agents.write_text("User rule\n\n" + agents.read_text(encoding="utf-8"), encoding="utf-8")
        result = service.uninstall()
        self.assertTrue(result["knowledge_preserved"])
        self.assertIn("User rule", agents.read_text(encoding="utf-8"))
        self.assertFalse((self.root / ".project-kb" / "mcp.json").exists())
        self.assertTrue((self.root / ".project-kb" / "curated" / "architecture.md").exists())

    def test_template_is_inferred_until_human_content_replaces_marker(self) -> None:
        service = ProjectService(self.root)
        service.initialize()
        architecture = self.root / ".project-kb" / "curated" / "architecture.md"
        self.assertIn("project-kb:template", architecture.read_text(encoding="utf-8"))
        with KnowledgeStore(service.db_path, readonly=True) as store:
            self.assertEqual(store.get_knowledge("curated.architecture").confidence, "inferred")

        architecture.write_text("# 架构\n\n经人工确认的架构边界。\n", encoding="utf-8")
        service.sync(task_summary="确认架构意图")
        with KnowledgeStore(service.db_path, readonly=True) as store:
            self.assertEqual(store.get_knowledge("curated.architecture").confidence, "verified")

    def test_commit_alignment_is_distinct_from_content_freshness(self) -> None:
        service = ProjectService(self.root)
        service.initialize()
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "tests@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Project KB Tests"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "baseline"], check=True)

        before = service.status()
        self.assertTrue(before["content_fresh"])
        self.assertFalse(before["commit_aligned"])
        self.assertFalse(service.check()[1])

        result = service.sync(task_summary="对齐提交元数据")
        self.assertEqual(result["changed_files"], [])
        self.assertTrue(result["commit_reconciled"])
        after = service.status()
        self.assertTrue(after["content_fresh"])
        self.assertTrue(after["commit_aligned"])
        self.assertTrue(service.check()[1])

    def test_large_module_reports_symbol_and_relation_truncation(self) -> None:
        functions = "\n".join(f"def function_{number}():\n    return helper()" for number in range(305))
        (self.root / "src" / "large.py").write_text("def helper():\n    return 1\n\n" + functions, encoding="utf-8")
        ProjectService(self.root).initialize()
        module = self.root / ".project-kb" / "generated" / "modules" / "large.py.md"
        content = module.read_text(encoding="utf-8")
        self.assertIn("符号内容已截断", content)
        self.assertIn("关系内容已截断", content)

    def test_doctor_and_check_report_unwired_configuration(self) -> None:
        from project_knowledge.config import ProjectConfig

        ProjectConfig(
            project_name="sample", embeddings="local", local_only=False, telemetry=True
        ).write(self.root)
        service = ProjectService(self.root)
        doctor = service.doctor()
        fields = {item["field"] for item in doctor["configuration_warnings"]}
        self.assertIn("retrieval.embeddings", fields)
        self.assertIn("privacy.local_only", fields)
        service.initialize()
        status, _ = service.check()
        self.assertEqual(status["configuration_warnings"], doctor["configuration_warnings"])


if __name__ == "__main__":
    unittest.main()
