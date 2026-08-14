from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from project_knowledge.codegraph import CodeGraphEngine
from project_knowledge.config import ProjectConfig
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.service import ProjectService
from project_knowledge.store import KnowledgeStore


APP = """
class Repository:
    def save(self, value):
        return value

def create_item(value):
    return Repository().save(value)

def read_item(value):
    return Repository().save(value)

def initialize():
    return True

def marker_update():
    return True

def bump_patch_version():
    return "next"

def read_project_version():
    return "current"
"""


class RetrievalWP06Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "app.py").write_text(APP, encoding="utf-8")
        (self.root / "tests" / "test_app.py").write_text(
            "from src.app import create_item\n\ndef test_create():\n    assert create_item('x')\n",
            encoding="utf-8",
        )
        (self.root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
        ProjectService(self.root).initialize()
        self.api = KnowledgeAPI(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_task_classification_context_explanation_and_reference_implementation(self) -> None:
        intent = self.api.classify_task("新增类似功能 create_item")
        self.assertEqual(intent["task_type"], "new_feature")
        self.assertIn("新增", intent["signals"])
        context = self.api.context("新增类似功能 create_item", max_tokens=1200)
        self.assertEqual(context["task_type"], "new_feature")
        self.assertTrue(context["likely_modules"])
        explanation = context["retrieval_explanation"]
        self.assertTrue(explanation["selected_records"])
        self.assertTrue(explanation["reference_implementations"])
        self.assertTrue(any("create_item" in item["symbol"] for item in explanation["reference_implementations"]))
        self.assertIn("extension_points", explanation)
        self.assertIn("unknowns", explanation)

    def test_chinese_identifier_phrases_recall_expected_exact_symbols(self) -> None:
        initialization = self.api.context("初始化项目时 config-v1 JSON Schema 如何由 all_schemas 发布", max_tokens=1000)
        self.assertTrue(any(item["name"] == "initialize" for item in initialization["symbols"]))

        markers = self.api.context("ProjectService.install 与 uninstall 如何管理客户端所有权标记", max_tokens=1000)
        self.assertTrue(any(item["name"] == "marker_update" for item in markers["symbols"]))

        versioning = self.api.context("补丁升级如何以核心版本为单一来源，同时同步 CHANGELOG 和 Codex 插件清单，dry-run 不修改文件", max_tokens=1000)
        names = {item["name"] for item in versioning["symbols"]}
        self.assertIn("bump_patch_version", names)
        self.assertIn("read_project_version", names)
        self.assertLessEqual(versioning["estimated_tokens"], versioning["token_budget"])

    def test_context_extracts_task_relevant_invariant_from_end_of_long_knowledge(self) -> None:
        knowledge_path = self.root / ".project-kb" / "curated" / "conventions.md"
        invariant = "同一发布批次的核心包版本与 Codex 插件版本必须一致。"
        original = knowledge_path.read_text(encoding="utf-8")
        noise = "\n".join(f"无关背景说明 {index}" for index in range(600))
        knowledge_path.write_text(original + "\n" + noise + "\n" + invariant + "\n", encoding="utf-8")
        ProjectService(self.root).sync()
        context = KnowledgeAPI(self.root).context(
            "补丁升级如何同步核心版本和 Codex 插件版本？",
            max_tokens=1400,
        )
        returned = "\n".join(item["content"] for item in context["knowledge"])
        self.assertIn(invariant, returned)

    def test_search_exposes_score_breakdown_and_impact_supports_bounded_multihop(self) -> None:
        results = self.api.search("Repository persistence")
        self.assertTrue(results["results"])
        first = results["results"][0]
        self.assertIn("score_breakdown", first)
        self.assertIn("why_selected", first)
        self.assertIn("text_match", first["score_breakdown"])
        impact = self.api.impact(files=["src/app.py"], max_hops=2, max_relations=50)
        self.assertEqual(impact["max_hops"], 2)
        self.assertIn("relation_hops", impact)
        self.assertIn("impact_explanation", impact)
        self.assertIn("tests/test_app.py", impact["affected_tests"])

    def test_codegraph_context_uses_engine_when_sqlite_symbols_are_empty(self) -> None:
        (self.root / "src" / "app.lua").write_text("local function login() end\n", encoding="utf-8")
        (self.root / "src" / "router.lua").write_text("local function route() end\n", encoding="utf-8")
        ProjectService(self.root).sync()
        api = KnowledgeAPI(self.root)
        api.config.engine = "codegraph"
        api.service.config.engine = "codegraph"
        client = Mock()
        client.project = self.root.resolve()
        client.command_display = "codegraph"
        client.status.return_value = {"initialized": True, "version": "1.5.0"}
        client.files.return_value = [
            {"path": "src/app.lua", "language": "lua"},
            {"path": "src/router.lua", "language": "lua"},
        ]
        client.query.return_value = [{
            "node": {
                "id": "src/app.lua::login", "name": "login", "kind": "function",
                "filePath": "src/app.lua", "startLine": 1,
            }
        }]
        client.impact.return_value = {
            "symbol": "login",
            "affected": [{
                "id": "src/router.lua::route", "name": "route", "kind": "function",
                "filePath": "src/router.lua", "startLine": 1,
            }],
        }
        client.affected_tests.return_value = {"affectedTests": ["tests/test_app.py"]}
        engine = CodeGraphEngine(ProjectConfig(engine="codegraph"))
        engine.client = client
        api.service.engine = engine
        with KnowledgeStore(api.service.db_path) as store:
            store.connection.execute("DELETE FROM relations")
            store.connection.execute("DELETE FROM symbols")
            store.connection.commit()

        result = api.context("修复 login 路由", max_tokens=2000)

        self.assertIn("src/app.lua::login", {item["id"] for item in result["symbols"]})
        self.assertIn("src/router.lua", result["impact"]["affected_files"])
        self.assertEqual(result["fact_source"], "codegraph")


if __name__ == "__main__":
    unittest.main()
