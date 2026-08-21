from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_knowledge.evaluate import load_dataset
from project_knowledge.mcp import TOOLS
from project_knowledge.models import (
    CanonicalFile,
    CanonicalSymbol,
    RetrievalCandidate,
)
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.service import ProjectService


SOURCE = """
class Repository:
    def save(self, value):
        return value

def create_item(value):
    return Repository().save(value)
"""


class RetrievalPhase0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text(SOURCE, encoding="utf-8")
        (self.root / "pyproject.toml").write_text(
            "[project]\nname='phase0-fixture'\n", encoding="utf-8"
        )
        ProjectService(self.root).initialize()
        self.api = KnowledgeAPI(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_canonical_models_publish_source_identity_and_explanations(self) -> None:
        file = CanonicalFile(
            repository_id="phase0-fixture",
            commit="abc123",
            path="src/app.py",
            language="python",
            module="src",
            file_hash="sha256:" + "a" * 64,
            status="fresh",
            metadata={"is_test": False, "is_generated": False, "is_vendor": False},
        )
        symbol = CanonicalSymbol(
            symbol_id="repo://phase0-fixture/abc123/src/app.py#create_item@6",
            qualified_name="create_item",
            short_name="create_item",
            kind="function",
            path="src/app.py",
            signature="create_item(value)",
            span={"start": 6, "end": 7},
            parent="",
            aliases=("create item",),
            source_commit="abc123",
            source_hash="sha256:" + "a" * 64,
            freshness="fresh",
        )
        candidate = RetrievalCandidate(
            candidate_id=symbol.symbol_id,
            file=file.path,
            symbol=symbol.qualified_name,
            channels=("symbol_exact", "graph_direct"),
            graph_paths=({"from": "caller", "edge": "calls", "to": symbol.symbol_id},),
            features={"exact_score": 1.0, "relation_score": 1.0},
            evidence=("src/app.py:6-7", "commit:abc123"),
            stage="ranked",
        )

        self.assertEqual(file.to_dict()["metadata"]["is_vendor"], False)
        self.assertEqual(symbol.to_dict()["span"], {"start": 6, "end": 7})
        self.assertEqual(candidate.to_dict()["channels"], ["symbol_exact", "graph_direct"])
        with self.assertRaises(ValueError):
            CanonicalFile(
                repository_id="phase0-fixture",
                commit="abc123",
                path="../outside.py",
                language="python",
                module="src",
                file_hash="sha256:" + "a" * 64,
            )

    def test_debug_trace_is_opt_in_and_covers_retrieval_stages(self) -> None:
        normal = self.api.context("create_item", max_tokens=1000)
        self.assertNotIn("retrieval_trace", normal)

        debug = self.api.context("create_item", max_tokens=1000, debug=True)
        trace = debug["retrieval_trace"]
        self.assertEqual(trace["schema_version"], 2)
        self.assertEqual(trace["query"]["raw"], "create_item")
        self.assertEqual(trace["query"]["intent"], "investigation")
        for stage in [
            "knowledge_recall",
            "symbol_recall",
            "file_recall",
            "canonical_dedup",
            "ranking",
            "context_assembly",
            "token_budget",
        ]:
            self.assertIn(stage, trace["stages"])
        self.assertGreater(trace["stages"]["file_recall"]["candidate_count"], 0)
        candidate = trace["stages"]["file_recall"]["candidates"][0]
        self.assertIn("candidate_id", candidate)
        self.assertTrue(candidate["channels"])
        self.assertIn("features", candidate)
        self.assertIn("snapshot_id", trace["source"])

    def test_search_and_impact_debug_traces_are_opt_in(self) -> None:
        search = self.api.search("app.py", debug=True)
        self.assertEqual(search["retrieval_trace"]["operation"], "knowledge_search")
        self.assertIn("lexical", search["retrieval_trace"]["channels"])
        self.assertNotIn("retrieval_trace", self.api.search("app.py"))

        impact = self.api.impact(files=["src/app.py"], max_hops=2, debug=True)
        self.assertEqual(impact["retrieval_trace"]["operation"], "knowledge_impact")
        self.assertEqual(impact["retrieval_trace"]["max_hops"], 2)
        self.assertIn("relation_count", impact["retrieval_trace"])

    def test_mcp_read_tools_accept_debug_without_relaxing_other_fields(self) -> None:
        schemas = {tool["name"]: tool["inputSchema"] for tool in TOOLS}
        for name in ("knowledge_context", "knowledge_search", "knowledge_impact"):
            self.assertEqual(schemas[name]["properties"]["debug"], {"type": "boolean"})
            self.assertFalse(schemas[name]["additionalProperties"])

    def test_gardenserver_phase0_dataset_and_snapshot_are_reproducible(self) -> None:
        samples = load_dataset(Path("evaluation/questions-gardenserver-phase0.jsonl"))
        snapshot = json.loads(
            Path("evaluation/snapshots/gardenserver-phase0.json").read_text(encoding="utf-8")
        )

        self.assertGreaterEqual(len(samples), 10)
        self.assertGreaterEqual(len({sample["category"] for sample in samples}), 5)
        self.assertTrue(all(sample.get("answer_status") == "verified" for sample in samples))
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["project"], "gardenserver")
        self.assertEqual(snapshot["codegraph"]["version"], "1.5.0")
        self.assertEqual(snapshot["codegraph"]["file_count"], 1296)
        self.assertEqual(snapshot["pks_scope"]["file_count"], 1270)
        self.assertRegex(snapshot["pks_scope"]["snapshot_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(snapshot["pks_scope"]["source_sha256"], r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
