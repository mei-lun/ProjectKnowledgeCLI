from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from project_knowledge.ranking import FileCandidate
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.service import ProjectService


SOURCE = """
class Repository:
    def save(self, value):
        return value

def create_item(value):
    return Repository().save(value)
"""


class RetrievalPhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "app.py").write_text(SOURCE, encoding="utf-8")
        (self.root / "tests" / "test_app.py").write_text(
            "from src.app import create_item\n\ndef test_create():\n    return create_item('x')\n",
            encoding="utf-8",
        )
        (self.root / "pyproject.toml").write_text(
            "[project]\nname='phase1-fixture'\n", encoding="utf-8"
        )
        ProjectService(self.root).initialize()
        self.api = KnowledgeAPI(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_query_aliases_expand_business_terms_without_replacing_original_terms(self) -> None:
        terms = self.api._symbol_terms("新增物品功能 create_item")
        aliases = self.api._query_aliases("新增物品功能 create_item", terms)

        self.assertIn("create_item", terms)
        self.assertIn("item", aliases)
        self.assertIn("create_item", aliases)
        self.assertEqual(terms[0], "create_item")

    def test_context_trace_reports_typed_recall_channels_and_limits(self) -> None:
        result = self.api.context("src/app.py create_item", max_tokens=1200, debug=True)
        trace = result["retrieval_trace"]
        channels = trace["stages"]["file_recall"]["channel_counts"]

        self.assertIn("path_exact", channels)
        self.assertIn("symbol_exact", channels)
        self.assertIn("lexical", channels)
        self.assertLessEqual(channels["path_exact"], 20)
        self.assertLessEqual(channels["symbol_exact"], 50)
        self.assertTrue(any("path_exact" in item["channels"] for item in trace["stages"]["file_recall"]["candidates"]))
        self.assertTrue(any("symbol_exact" in item["channels"] for item in trace["stages"]["file_recall"]["candidates"]))

    def test_channel_limit_keeps_candidates_from_distinct_channels(self) -> None:
        candidates = [
            FileCandidate(path=f"src/{index}.py", stages={"lexical"}, channels={"lexical"}, original_order=index)
            for index in range(4)
        ]
        candidates.append(
            FileCandidate(path="src/exact.py", stages={"path_exact"}, channels={"path_exact"}, original_order=9)
        )

        limited = self.api._limit_recall_candidates(
            candidates,
            {"lexical": 2, "path_exact": 20},
        )
        self.assertEqual([item.path for item in limited], ["src/0.py", "src/1.py", "src/exact.py"])

    def test_typed_graph_expansion_distinguishes_direct_and_multihop(self) -> None:
        candidates, _ = self.api._context_file_candidates(
            "create_item",
            {"task_type": "impact_analysis"},
            [],
            {
                "relations": [
                    {
                        "source": "src/app.py::create_item",
                        "target": "tests/test_app.py::test_create",
                        "path": "src/app.py",
                        "hop": 2,
                    }
                ],
                "affected_files": ["src/app.py"],
                "dependency_files": [],
                "affected_tests": ["tests/test_app.py"],
            },
            [],
        )

        source = next(item for item in candidates if item.path == "src/app.py" and "impact" in item.stages)
        test = next(item for item in candidates if item.path == "tests/test_app.py")
        self.assertEqual(source.graph_hop, 2)
        self.assertIn("graph_multihop", source.channels)
        self.assertIn("test_config", test.channels)

    def test_pending_files_are_not_recall_candidates(self) -> None:
        pending = self.root / "src" / "pending.py"
        pending.write_text("def pending():\n    return True\n", encoding="utf-8")
        result = ProjectService(self.root).sync()
        self.assertIn("src/pending.py", result["changed_files"])
        status = self.api.status()
        self.assertEqual(status["pending_files"], [])

        pending.write_text("def pending():\n    return False\n", encoding="utf-8")
        candidates, _ = self.api._context_file_candidates(
            "pending",
            {"task_type": "investigation"},
            [],
            {"relations": [], "affected_files": [], "dependency_files": [], "affected_tests": []},
            [],
        )
        self.assertFalse(any(item.path == "src/pending.py" for item in candidates))


if __name__ == "__main__":
    unittest.main()
