from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from project_knowledge.cli import main
from project_knowledge.codegraph import CodeGraphClient, CodeGraphCommand, CodeGraphCommandResolver
from project_knowledge.config import ProjectConfig
from project_knowledge.evaluate import (
    evaluate,
    evaluate_quality_gate,
    evaluate_suite,
    _retrieve,
    _select_markdown_pages,
    load_dataset,
)
from project_knowledge.performance import run_performance_harness
from project_knowledge.real_project import run_readonly_mirror
from project_knowledge.ranking import rank_files
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.service import ProjectService


SOURCE = '''
class Repository:
    def save(self, value):
        return value

class AccountService:
    def login(self, player_id):
        return Repository().save(player_id)
'''


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text(SOURCE, encoding="utf-8")
        (self.root / "pyproject.toml").write_text("[project]\nname='evaluation-fixture'\n", encoding="utf-8")
        ProjectService(self.root).initialize()
        self.dataset = self.root / "questions.jsonl"
        self.dataset.write_text(
            json.dumps({
                "schema_version": 1,
                "id": "account-login",
                "task": "AccountService.login 如何调用 Repository.save？",
                "category": "call_path",
                "expected_files": ["src/app.py"],
                "expected_symbols": ["src/app.py::AccountService.login"],
                "expected_call_path": [
                    "src/app.py::AccountService.login",
                    "src/app.py::Repository.save",
                ],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dataset_requires_stable_ids_categories_and_expectations(self) -> None:
        loaded = load_dataset(self.dataset)
        self.assertEqual(loaded[0]["id"], "account-login")
        invalid = self.root / "invalid.jsonl"
        invalid.write_text('{"task":"missing metadata"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 1"):
            load_dataset(invalid)

    def test_report_contains_anchor_semantic_cost_and_reproducibility_metrics(self) -> None:
        report = evaluate(self.root, self.dataset, strategy="hybrid")
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["strategy"], "hybrid")
        self.assertEqual(report["samples"], 1)
        self.assertIn("file_recall", report["metrics"])
        self.assertIn("call_path_recall", report["metrics"])
        self.assertIn("success_rate", report["metrics"])
        self.assertIn("average_tool_calls", report["metrics"])
        self.assertIn("p95_latency_ms", report["metrics"])
        self.assertIn("dataset_sha256", report["reproducibility"])
        self.assertGreaterEqual(report["reproducibility"]["project_files"], 2)
        self.assertEqual(report["results"][0]["id"], "account-login")

    def test_strategy_suite_isolates_grep_code_markdown_and_unavailable_codegraph(self) -> None:
        suite = evaluate_suite(
            self.root,
            self.dataset,
            strategies=["hybrid", "grep_read", "code", "markdown", "codegraph"],
        )
        self.assertEqual(set(suite["strategies"]), {"hybrid", "grep_read", "code", "markdown", "codegraph"})
        self.assertFalse(suite["strategies"]["codegraph"]["available"])
        self.assertEqual(suite["strategies"]["codegraph"]["reason_code"], "adapter_unavailable")
        self.assertIn("engine_not_selected", suite["strategies"]["codegraph"]["details"])
        self.assertTrue(suite["strategies"]["grep_read"]["available"])
        self.assertIn("src/app.py", suite["strategies"]["grep_read"]["results"][0]["returned_files"])

    def test_codegraph_strategy_evaluates_when_adapter_is_available(self) -> None:
        config = ProjectConfig.load(self.root)
        config.engine = "codegraph"
        config.write(self.root)
        node = {
            "id": "src/app.py::AccountService.login",
            "name": "login",
            "kind": "method",
            "filePath": "src/app.py",
            "startLine": 7,
        }
        with (
            patch.object(
                CodeGraphCommandResolver,
                "resolve",
                return_value=CodeGraphCommand(("codegraph",), "codegraph"),
            ),
            patch.object(CodeGraphClient, "status", return_value={"initialized": True, "version": "1.5.0"}),
            patch.object(CodeGraphClient, "files", return_value=[{"path": "src/app.py", "language": "python"}]),
            patch.object(CodeGraphClient, "query", return_value=[{"node": node}]),
            patch.object(CodeGraphClient, "impact", return_value={"symbol": "login", "affected": [node]}),
            patch.object(CodeGraphClient, "affected_tests", return_value={"affectedTests": []}),
        ):
            report = evaluate(self.root, self.dataset, strategy="codegraph")

        self.assertTrue(report["available"])
        self.assertEqual(report["strategy"], "codegraph")
        self.assertEqual(report["reproducibility"]["engine"]["engine"], "codegraph")

    def test_quality_gate_checks_thresholds_and_baseline_regression(self) -> None:
        report = {
            "strategies": {
                "hybrid": {"available": True, "samples": 20, "metrics": {
                    "file_recall": 0.7,
                    "success_rate": 0.5,
                    "average_context_tokens": 1000,
                }},
                "codegraph": {"available": False, "reason_code": "adapter_unavailable"},
            }
        }
        thresholds = {
            "minimum_samples": 20,
            "required_strategies": ["hybrid"],
            "allowed_unavailable_strategies": ["codegraph"],
            "minimum": {"file_recall": 0.6, "success_rate": 0.4},
            "maximum": {"average_context_tokens": 1200},
            "strategies": {"hybrid": {"minimum": {"file_recall": 0.65}}},
            "allowed_regression": {"file_recall": 0.02},
        }
        baseline = {"strategies": {"hybrid": {"metrics": {"file_recall": 0.75}}}}
        gate = evaluate_quality_gate(report, thresholds, baseline)
        self.assertFalse(gate["passed"])
        self.assertTrue(any(item["code"] == "metric_regression" for item in gate["failures"]))

        baseline["strategies"]["hybrid"]["metrics"]["file_recall"] = 0.71
        self.assertTrue(evaluate_quality_gate(report, thresholds, baseline)["passed"])

    def test_quality_gate_does_not_compare_aggregate_regression_across_different_datasets(self) -> None:
        report = {
            "dataset_sha256": "sha256:new",
            "strategies": {"hybrid": {"available": True, "samples": 25, "metrics": {"file_recall": 0.7}}},
        }
        baseline = {
            "dataset_sha256": "sha256:old",
            "strategies": {"hybrid": {"metrics": {"file_recall": 0.9}}},
        }
        thresholds = {
            "minimum_samples": 20,
            "required_strategies": ["hybrid"],
            "minimum": {"file_recall": 0.6},
            "allowed_regression": {"file_recall": 0.02},
        }
        gate = evaluate_quality_gate(report, thresholds, baseline)
        self.assertTrue(gate["passed"])
        self.assertFalse(any(item["code"] == "metric_regression" for item in gate["failures"]))
        self.assertTrue(any(item["code"] == "baseline_dataset_mismatch" for item in gate["warnings"]))

    def test_markdown_page_selection_keeps_a_source_rich_code_module(self) -> None:
        results = [
            {"id": "curated.provider.and.evidence", "kind": "curated", "score": 8.0},
            {"id": "generated.module.tests", "kind": "module", "score": 7.0},
            {"id": "decision.0002.codegraph.adapter.boundary", "kind": "decision", "score": 6.0},
            {"id": "generated.module.project_knowledge", "kind": "module", "score": 5.0},
        ]
        selected = _select_markdown_pages(results, limit=3)
        selected_ids = [item["id"] for item in selected]
        self.assertIn("generated.module.project_knowledge", selected_ids)
        self.assertEqual(len(selected), 3)
        results[-1]["score"] = 2.0
        low_relevance = _select_markdown_pages(results, limit=3)
        self.assertNotIn("generated.module.project_knowledge", [item["id"] for item in low_relevance])

    def test_all_available_strategies_return_ranking_contract(self) -> None:
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]

        for strategy in ("hybrid", "code", "markdown", "grep_read"):
            result = _retrieve(api, sample, strategy)
            self.assertIn("core_files", result)
            self.assertIn("file_rankings", result)
            self.assertEqual(
                result["files"],
                list(dict.fromkeys(result["core_files"] + result["supporting_files"])),
            )
            self.assertEqual(result["ranking_status"], "ok")

    def test_markdown_and_grep_delegate_ordering_to_production_ranker(self) -> None:
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]
        real_rank = rank_files
        calls = []

        def record_rank(candidates, **kwargs):
            calls.append([candidate.path for candidate in candidates])
            return real_rank(candidates, **kwargs)

        with patch("project_knowledge.evaluate.rank_files", side_effect=record_rank):
            _retrieve(api, sample, "markdown")
            _retrieve(api, sample, "grep_read")

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(candidates for candidates in calls))

    def test_markdown_strategy_respects_sample_token_budget(self) -> None:
        sample = json.loads(self.dataset.read_text(encoding="utf-8"))
        sample["max_tokens"] = 256
        self.dataset.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
        report = evaluate(self.root, self.dataset, strategy="markdown")
        self.assertLessEqual(report["results"][0]["estimated_context_tokens"], 256)

    def test_cli_returns_two_when_quality_gate_fails(self) -> None:
        thresholds = self.root / "thresholds.json"
        thresholds.write_text(json.dumps({
            "minimum_samples": 20,
            "required_strategies": ["hybrid"],
            "minimum": {},
            "maximum": {},
        }), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            exit_code = main([
                "evaluate", str(self.dataset), "--project", str(self.root),
                "--strategy", "hybrid", "--thresholds", str(thresholds), "--quiet",
            ])
        self.assertEqual(exit_code, 2)

    def test_small_performance_harness_reports_percentiles_and_stale_probe(self) -> None:
        report = run_performance_harness([10], repetitions=2)
        result = report["results"][0]
        self.assertEqual(result["file_count"], 10)
        self.assertIn("p95_ms", result["status"])
        self.assertIn("p95_ms", result["context"])
        self.assertEqual(result["stale_detection"]["passed"], True)

    def test_real_project_harness_indexes_temporary_mirror_without_writing_source(self) -> None:
        source = self.root / "real-source"
        source.mkdir()
        for number in range(5):
            (source / f"service_{number}.lua").write_text(
                f"local M = {{}}\nfunction M.run() return {number} end\nreturn M\n",
                encoding="utf-8",
            )
        svn = source / ".svn"
        svn.mkdir()
        (svn / "entries").write_text("private metadata", encoding="utf-8")
        before = sorted(path.relative_to(source).as_posix() for path in source.rglob("*"))
        report = run_readonly_mirror(source)
        after = sorted(path.relative_to(source).as_posix() for path in source.rglob("*"))
        self.assertEqual(before, after)
        self.assertTrue(report["source"]["unchanged"])
        self.assertEqual(report["source"]["discovered_files"], 5)
        self.assertEqual(report["mirror_initialization"]["source_files"], 5)
        self.assertEqual(report["mirror_initialization"]["indexed_files"], 6)


if __name__ == "__main__":
    unittest.main()
