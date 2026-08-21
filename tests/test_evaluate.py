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
    _aggregate,
    _evaluate_sample,
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


ORIGINAL_EXPECTED_FILES = (
    ("self-init-flow", ("src/project_knowledge/service.py", "src/project_knowledge/knowledge.py", "src/project_knowledge/store.py")),
    ("self-incremental-sync", ("src/project_knowledge/service.py", "src/project_knowledge/engine.py", "src/project_knowledge/knowledge.py")),
    ("self-atomic-rebuild", ("src/project_knowledge/service.py", "src/project_knowledge/store.py")),
    ("self-commit-alignment", ("src/project_knowledge/service.py", "src/project_knowledge/util.py")),
    ("self-template-confidence", ("src/project_knowledge/knowledge.py", "src/project_knowledge/models.py")),
    ("self-module-truncation", ("src/project_knowledge/knowledge.py",)),
    ("self-runtime-schema", ("src/project_knowledge/schemas.py", "src/project_knowledge/knowledge.py", "src/project_knowledge/service.py")),
    ("self-config-capabilities", ("src/project_knowledge/config.py", "src/project_knowledge/service.py")),
    ("self-codegraph-failure", ("src/project_knowledge/codegraph.py", "docs/knowledge/decisions/0002-codegraph-adapter-boundary.md")),
    ("self-python-parser", ("src/project_knowledge/engine.py", "src/project_knowledge/models.py")),
    ("self-generic-parser", ("src/project_knowledge/engine.py", "src/project_knowledge/models.py")),
    ("self-context-budget", ("src/project_knowledge/retrieval.py", "src/project_knowledge/store.py")),
    ("self-stale-shield", ("src/project_knowledge/retrieval.py", "src/project_knowledge/service.py")),
    ("self-impact-analysis", ("src/project_knowledge/retrieval.py", "src/project_knowledge/store.py")),
    ("self-mcp-dispatch", ("src/project_knowledge/mcp.py", "src/project_knowledge/retrieval.py")),
    ("self-manifest-publication", ("src/project_knowledge/knowledge.py", "src/project_knowledge/models.py", "src/project_knowledge/schemas.py")),
    ("self-version-bump", ("src/project_knowledge/versioning.py", "src/project_knowledge/__init__.py", "CHANGELOG.md")),
    ("self-owned-integration", ("src/project_knowledge/service.py", "src/project_knowledge/util.py")),
    ("self-evaluation-gate", ("src/project_knowledge/evaluate.py", "src/project_knowledge/cli.py")),
    ("self-performance-harness", ("src/project_knowledge/performance.py", "evaluation/performance_harness.py")),
    ("self-evidence-pack", ("src/project_knowledge/evidence.py", "src/project_knowledge/models.py", "src/project_knowledge/schemas.py")),
    ("self-provider-authorization", ("src/project_knowledge/provider.py", "src/project_knowledge/config.py")),
    ("self-provider-runtime", ("src/project_knowledge/provider.py", "src/project_knowledge/schemas.py", "src/project_knowledge/util.py")),
    ("self-provider-preview", ("src/project_knowledge/cli.py", "src/project_knowledge/evidence.py", "src/project_knowledge/provider.py")),
    ("self-provider-extension", ("src/project_knowledge/provider.py",)),
    ("self-feature-guide-schema", ("src/project_knowledge/schemas.py", "src/project_knowledge/semantic.py")),
    ("self-semantic-generation", ("src/project_knowledge/semantic.py", "src/project_knowledge/provider.py", "src/project_knowledge/knowledge.py")),
    ("self-feature-source-validation", ("src/project_knowledge/semantic.py", "src/project_knowledge/evidence.py")),
    ("self-draft-lifecycle", ("src/project_knowledge/knowledge.py", "src/project_knowledge/service.py", "src/project_knowledge/retrieval.py", "src/project_knowledge/semantic.py")),
    ("self-feature-retrieval", ("src/project_knowledge/semantic.py", "src/project_knowledge/retrieval.py", "src/project_knowledge/cli.py")),
    ("self-proposal-stable-id", ("src/project_knowledge/proposal.py", "src/project_knowledge/models.py", "src/project_knowledge/schemas.py")),
    ("self-proposal-apply-conflict", ("src/project_knowledge/proposal.py", "src/project_knowledge/util.py")),
    ("self-draft-proposal-promotion", ("src/project_knowledge/semantic.py", "src/project_knowledge/proposal.py", "src/project_knowledge/cli.py")),
    ("self-proposal-delete-adr", ("src/project_knowledge/models.py", "src/project_knowledge/schemas.py", "src/project_knowledge/proposal.py")),
    ("self-semantic-update-queue", ("src/project_knowledge/service.py", "src/project_knowledge/proposal.py")),
    ("self-task-classification", ("src/project_knowledge/retrieval.py",)),
    ("self-retrieval-explanation", ("src/project_knowledge/retrieval.py",)),
    ("self-bounded-multihop", ("src/project_knowledge/retrieval.py",)),
    ("self-feature-development-context", ("src/project_knowledge/retrieval.py",)),
    ("self-context-unknowns", ("src/project_knowledge/retrieval.py",)),
)

HARD_NEGATIVE_IDS = (
    "self-ranking-exact-over-test-noise",
    "self-ranking-qualified-symbol",
    "self-ranking-path-over-content-frequency",
    "self-ranking-one-hop-over-two-hop",
    "self-ranking-direct-knowledge-source",
    "self-context-core-supporting-contract",
    "self-ranking-fallback-gate",
    "self-ranking-stale-shield",
    "self-evaluation-production-ranker",
    "self-ranking-token-core-protection",
)


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

    def test_wp12a_dataset_preserves_original_answers_and_adds_hard_negatives(self) -> None:
        samples = load_dataset(Path("evaluation/questions.jsonl"))

        self.assertEqual(len(samples), 50)
        self.assertEqual(
            tuple((item["id"], tuple(item["expected_files"])) for item in samples[:40]),
            ORIGINAL_EXPECTED_FILES,
        )
        self.assertEqual(tuple(item["id"] for item in samples[40:]), HARD_NEGATIVE_IDS)
        self.assertTrue(all("acceptable_supporting_files" not in item for item in samples[40:]))

    def test_wp12a_absolute_thresholds_freeze_approved_precision_and_cost_gates(self) -> None:
        thresholds = json.loads(Path("evaluation/thresholds.json").read_text(encoding="utf-8"))

        self.assertEqual(thresholds["frozen_for_version"], "0.1.29")
        self.assertEqual(thresholds["minimum_samples"], 50)
        self.assertEqual(
            thresholds["strategies"]["hybrid"]["minimum"],
            {
                "file_recall": 0.94,
                "file_precision": 0.22,
                "core_file_recall": 0.85,
                "core_file_precision": 0.40,
                "symbol_recall": 0.8,
                "call_path_recall": 1.0,
                "extension_point_recall": 0.5,
                "invariant_recall": 0.14,
                "design_reason_recall": 1.0,
                "success_rate": 0.4,
            },
        )
        self.assertEqual(
            thresholds["strategies"]["hybrid"]["maximum"],
            {
                "average_context_tokens": 1000,
                "average_tool_calls": 4.0,
                "average_returned_files": 10,
                "ranking_fallback_rate": 0.0,
            },
        )
        for strategy, file_precision in (("code", 0.25), ("markdown", 0.30), ("grep_read", 0.32)):
            self.assertEqual(thresholds["strategies"][strategy]["minimum"]["file_precision"], file_precision)
            self.assertEqual(thresholds["strategies"][strategy]["maximum"]["ranking_fallback_rate"], 0.0)
        self.assertEqual(thresholds["allowed_regression"]["core_file_recall"], 0.02)
        self.assertEqual(thresholds["allowed_regression"]["core_file_precision"], 0.02)
        self.assertEqual(thresholds["allowed_regression"]["ndcg_at_5"], 0.02)
        self.assertEqual(thresholds["allowed_regression"]["average_core_files"], 0.5)
        self.assertEqual(thresholds["allowed_regression"]["average_returned_files"], 0.5)
        self.assertEqual(thresholds["allowed_regression"]["ranking_fallback_rate"], 0.0)

    def test_dataset_requires_stable_ids_categories_and_expectations(self) -> None:
        loaded = load_dataset(self.dataset)
        self.assertEqual(loaded[0]["id"], "account-login")
        labeled = json.loads(self.dataset.read_text(encoding="utf-8"))
        labeled["acceptable_supporting_files"] = ["tests/test_app.py"]
        self.dataset.write_text(json.dumps(labeled, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(load_dataset(self.dataset)[0]["acceptable_supporting_files"], ["tests/test_app.py"])
        invalid = self.root / "invalid.jsonl"
        invalid.write_text('{"task":"missing metadata"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 1"):
            load_dataset(invalid)
        invalid.write_text(json.dumps({
            **labeled,
            "acceptable_supporting_files": [""],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 1"):
            load_dataset(invalid)

    def test_dataset_v2_validates_required_evidence_and_ordered_path_continuity(self) -> None:
        sample = json.loads(self.dataset.read_text(encoding="utf-8"))
        sample.update({
            "schema_version": 2,
            "required_evidence": {
                "symbols": [
                    {"id": "src/app.py::AccountService.login", "signature": "login(self)"},
                ],
                "relation_paths": [{
                    "path_id": "login-save",
                    "edges": [
                        {"source": "src/app.py::AccountService.login", "kind": "calls",
                         "target": "src/app.py::Repository.save"},
                    ],
                }],
            },
        })
        self.dataset.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")

        loaded = load_dataset(self.dataset)
        self.assertEqual(loaded[0]["schema_version"], 2)
        self.assertEqual(loaded[0]["required_evidence"]["symbols"][0]["id"],
                         "src/app.py::AccountService.login")

        invalid = self.root / "invalid-v2.jsonl"
        sample["required_evidence"]["relation_paths"][0]["edges"] = [
            {"source": "A", "kind": "calls", "target": "B"},
            {"source": "C", "kind": "calls", "target": "D"},
        ]
        invalid.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "relation_paths.*连续"):
            load_dataset(invalid)

    def test_v2_metrics_report_required_retention_and_incomplete_consistency(self) -> None:
        api = KnowledgeAPI(self.root)
        sample = json.loads(self.dataset.read_text(encoding="utf-8"))
        sample.update({
            "schema_version": 2,
            "expected_context_incomplete": True,
            "required_evidence": {
                "symbols": [{"id": "symbol:A"}, {"id": "symbol:B"}],
                "relation_paths": [{
                    "edges": [
                        {"source": "symbol:A", "kind": "calls", "target": "symbol:B"},
                    ],
                }],
            },
            "line": 1,
        })
        returned = {
            "files": ["src/app.py"], "core_files": ["src/app.py"], "supporting_files": [],
            "optional_files": [], "symbols": {"symbol:A"}, "call_path": {"symbol:A"},
            "text": "", "tool_calls": 1, "stale_detected": False,
            "selection_reasons": {}, "file_rankings": [], "ranking_status": "ok",
            "pre_required_evidence": {
                "symbols": [
                    {"id": "symbol:A", "signature": "def a()", "span": {"start_line": 1, "end_line": 2}},
                    {"id": "symbol:B", "signature": "def b()", "span": {"start_line": 4, "end_line": 5}},
                ],
                "relation_paths": [{"edges": [
                    {"source": "symbol:A", "kind": "calls", "target": "symbol:B"},
                ]}],
            },
            "post_required_evidence": {
                "symbols": [
                    {"id": "symbol:A", "signature": "def a()", "span": {"start_line": 1, "end_line": 2}},
                ],
                "relation_paths": [],
            },
            "context_incomplete": True,
        }
        with patch("project_knowledge.evaluate._retrieve", return_value=returned):
            result = _evaluate_sample(api, sample, "hybrid")

        self.assertEqual(result["metrics"]["required_symbol_label_recall"], 1.0)
        self.assertEqual(result["metrics"]["required_relation_path_label_recall"], 1.0)
        self.assertEqual(result["metrics"]["pre_required_symbol_recall"], 1.0)
        self.assertEqual(result["metrics"]["post_required_symbol_recall"], 0.5)
        self.assertEqual(result["metrics"]["required_symbol_retention"], 0.5)
        self.assertEqual(result["metrics"]["required_symbol_payload_retention"], 0.5)
        self.assertEqual(result["metrics"]["required_relation_path_retention"], 0.0)
        self.assertEqual(result["metrics"]["context_incomplete_consistency"], 1.0)
        self.assertIn("required_symbol_retention", result["failed_metrics"])
        self.assertIn("required_symbol_payload_retention", result["failed_metrics"])
        self.assertIn("required_relation_path_retention", result["failed_metrics"])

    def test_core_metrics_are_strict_and_supporting_labels_are_diagnostic(self) -> None:
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]
        sample["acceptable_supporting_files"] = ["tests/test_app.py"]
        returned = {
            "files": ["src/app.py", "tests/test_app.py", "README.md"],
            "core_files": ["README.md", "src/app.py"],
            "supporting_files": ["tests/test_app.py"],
            "symbols": {"src/app.py::AccountService.login"},
            "call_path": set(sample["expected_call_path"]),
            "text": "",
            "tool_calls": 1,
            "stale_detected": False,
            "selection_reasons": {},
            "file_rankings": [],
            "ranking_status": "ok",
        }

        with patch("project_knowledge.evaluate._retrieve", return_value=returned):
            result = _evaluate_sample(api, sample, "hybrid")

        self.assertEqual(result["metrics"]["core_file_recall"], 1.0)
        self.assertEqual(result["metrics"]["core_file_precision"], 0.5)
        self.assertEqual(result["metrics"]["file_precision"], 0.333333)
        self.assertEqual(result["metrics"]["acceptable_supporting_precision"], 1.0)
        self.assertEqual(result["metrics"]["ndcg_at_5"], 0.63093)
        self.assertEqual(result["core_failed_metrics"], [])
        self.assertEqual(result["core_files"], returned["core_files"])
        self.assertEqual(result["supporting_files"], returned["supporting_files"])
        self.assertEqual(result["file_rankings"], returned["file_rankings"])
        self.assertEqual(result["ranking_status"], "ok")

    def test_core_metrics_are_zero_without_expected_files(self) -> None:
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]
        sample["expected_files"] = []
        returned = {
            "files": ["src/app.py"],
            "core_files": ["src/app.py"],
            "supporting_files": [],
            "symbols": {"src/app.py::AccountService.login"},
            "call_path": set(sample["expected_call_path"]),
            "text": "",
            "tool_calls": 1,
            "stale_detected": False,
            "selection_reasons": {},
            "file_rankings": [],
            "ranking_status": "ok",
        }

        with patch("project_knowledge.evaluate._retrieve", return_value=returned):
            result = _evaluate_sample(api, sample, "hybrid")

        self.assertEqual(result["metrics"]["core_file_precision"], 0.0)
        self.assertEqual(result["metrics"]["core_file_recall"], 0.0)
        self.assertEqual(result["metrics"]["ndcg_at_5"], 0.0)
        self.assertEqual(result["core_failed_metrics"], [])
        self.assertTrue(result["success"])

    def test_ranking_fallback_is_a_sample_failure(self) -> None:
        report = {
            "strategies": {
                "hybrid": {
                    "available": True,
                    "samples": 50,
                    "metrics": {"ranking_fallback_rate": 0.02},
                }
            }
        }
        thresholds = {
            "minimum_samples": 50,
            "required_strategies": ["hybrid"],
            "maximum": {"ranking_fallback_rate": 0.0},
        }

        gate = evaluate_quality_gate(report, thresholds)

        self.assertFalse(gate["passed"])
        self.assertTrue(any(item["metric"] == "ranking_fallback_rate" for item in gate["failures"]))

    def test_aggregate_reports_core_file_counts_and_fallback_rate(self) -> None:
        metrics, counts = _aggregate([
            {
                "metrics": {"ranking_fallback": 0.0},
                "core_files": ["src/app.py"],
                "returned_files": ["src/app.py", "README.md"],
                "success": True,
                "estimated_context_tokens": 10,
                "tool_calls": 1,
                "latency_ms": 1.0,
            },
            {
                "metrics": {"ranking_fallback": 1.0},
                "core_files": ["src/app.py", "src/other.py"],
                "returned_files": ["src/app.py"],
                "success": False,
                "estimated_context_tokens": 20,
                "tool_calls": 2,
                "latency_ms": 2.0,
            },
        ])

        self.assertEqual(metrics["average_core_files"], 1.5)
        self.assertEqual(metrics["average_returned_files"], 1.5)
        self.assertEqual(metrics["ranking_fallback_rate"], 0.5)
        self.assertEqual(counts["ranking_fallback_rate"], 2)

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

    def test_strategy_suite_isolates_grep_code_markdown_and_real_codegraph(self) -> None:
        suite = evaluate_suite(
            self.root,
            self.dataset,
            strategies=["hybrid", "grep_read", "code", "markdown", "codegraph"],
        )
        self.assertEqual(set(suite["strategies"]), {"hybrid", "grep_read", "code", "markdown", "codegraph"})
        self.assertTrue(suite["strategies"]["codegraph"]["available"])
        self.assertEqual(suite["strategies"]["codegraph"]["metrics"]["ranking_fallback_rate"], 0.0)
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

    def test_quality_gate_treats_file_counts_as_lower_is_better_costs(self) -> None:
        thresholds = {
            "minimum_samples": 1,
            "required_strategies": ["code"],
            "allowed_regression": {
                "average_core_files": 0.5,
                "average_returned_files": 0.5,
            },
        }
        baseline = {"strategies": {"code": {"metrics": {
            "average_core_files": 5.0,
            "average_returned_files": 7.0,
        }}}}
        improved = {"strategies": {"code": {
            "available": True,
            "samples": 1,
            "metrics": {"average_core_files": 4.0, "average_returned_files": 6.0},
        }}}
        regressed = {"strategies": {"code": {
            "available": True,
            "samples": 1,
            "metrics": {"average_core_files": 6.0, "average_returned_files": 8.0},
        }}}

        self.assertTrue(evaluate_quality_gate(improved, thresholds, baseline)["passed"])
        failures = evaluate_quality_gate(regressed, thresholds, baseline)["failures"]
        self.assertEqual(
            {item["metric"] for item in failures if item["code"] == "metric_regression"},
            {"average_core_files", "average_returned_files"},
        )

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

        for strategy in ("hybrid", "code", "markdown", "grep_read", "codegraph"):
            result = _retrieve(api, sample, strategy)
            self.assertIn("core_files", result)
            self.assertIn("file_rankings", result)
            self.assertEqual(
                result["files"],
                list(dict.fromkeys(result["core_files"] + result["supporting_files"])),
            )
            self.assertEqual(result["ranking_status"], "ok")

    def test_hybrid_and_codegraph_forward_context_ranking_and_selection_reasons(self) -> None:
        """Catch evaluator reranking, repartitioning, or reason loss after context()."""
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]
        context = api.context(sample["task"], sample.get("max_tokens", 4000))
        expected_reasons = {
            item["path"]: {
                "stage": item["selection_stage"],
                "anchor": item["why_selected"],
            }
            for item in context["file_rankings"]
        }

        for strategy in ("hybrid", "codegraph"):
            result = _retrieve(api, sample, strategy)
            self.assertEqual(result["core_files"], context["core_files"])
            self.assertEqual(result["supporting_files"], context["supporting_files"])
            self.assertEqual(result["files"], context["files"])
            self.assertEqual(result["file_rankings"], context["file_rankings"])
            self.assertEqual(result["ranking_status"], context["ranking_status"])
            self.assertEqual(result["selection_reasons"], expected_reasons)
            self.assertEqual(set(result["selection_reasons"]), set(result["files"]))

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
        # A Markdown search may legitimately find no fresh selected page after
        # the stale-evidence shield. Both strategies must still delegate their
        # final ordering, while grep over this fixture has concrete matches.
        self.assertTrue(calls[1])

    def test_markdown_expands_cited_sources_with_bounded_live_impact(self) -> None:
        dependency_paths = []
        for index in range(13):
            path = f"src/helper_{index:02d}.py"
            (self.root / path).write_text(
                f"def persist_{index}(value): return value\n",
                encoding="utf-8",
            )
            dependency_paths.append(path)
        ProjectService(self.root).sync()
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]
        search_result = {
            "id": "generated.feature.account-login",
            "kind": "feature-guide",
            "score": 9.0,
            "summary": "Account login is implemented in `src/app.py`.",
            "sources": [{"type": "file", "path": "src/app.py"}],
        }
        record = {
            **search_result,
            "status": "fresh",
            "freshness": "fresh",
            "requires_live_source": False,
            "content": "Account login is implemented in `src/app.py`.",
        }
        impact = {
            "relations": [],
            "affected_files": dependency_paths,
            "dependency_files": dependency_paths,
            "affected_tests": [],
        }
        captured = []
        real_rank = rank_files

        def record_rank(candidates, **kwargs):
            captured.append([candidate.path for candidate in candidates])
            return real_rank(candidates, **kwargs)

        with (
            patch.object(api, "search", return_value={"results": [search_result]}),
            patch.object(api, "get", return_value=record),
            patch.object(api, "impact", return_value=impact) as impact_mock,
            patch("project_knowledge.evaluate.rank_files", side_effect=record_rank),
        ):
            _retrieve(api, sample, "markdown")

        self.assertEqual(len(captured), 1)
        self.assertIn("src/app.py", captured[0])
        self.assertIn("src/helper_12.py", captured[0])
        impact_mock.assert_called_once_with(
            files=["src/app.py"], max_hops=2, max_relations=100
        )

    def test_markdown_reserves_budget_for_each_selected_page_source(self) -> None:
        (self.root / "src" / "helper.py").write_text(
            "def persist(value): return value\n",
            encoding="utf-8",
        )
        ProjectService(self.root).sync()
        api = KnowledgeAPI(self.root)
        sample = {**load_dataset(self.dataset)[0], "max_tokens": 256}
        results = [
            {
                "id": "generated.module.large",
                "kind": "module",
                "score": 10.0,
                "summary": "src/app.py",
                "sources": [{"type": "file", "path": "src/app.py"}],
            },
            {
                "id": "curated.feature.helper",
                "kind": "feature-guide",
                "score": 9.0,
                "summary": "src/helper.py",
                "sources": [{"type": "file", "path": "src/helper.py"}],
            },
        ]
        records = {
            "generated.module.large": {
                **results[0], "status": "fresh", "freshness": "fresh",
                "requires_live_source": False,
                "content": ("AccountService login src/app.py\n" * 500),
            },
            "curated.feature.helper": {
                **results[1], "status": "fresh", "freshness": "fresh",
                "requires_live_source": False,
                "content": "Repository save is implemented in src/helper.py.",
            },
        }
        captured = []
        real_rank = rank_files

        def record_rank(candidates, **kwargs):
            captured.append([candidate.path for candidate in candidates])
            return real_rank(candidates, **kwargs)

        with (
            patch.object(api, "search", return_value={"results": results}),
            patch.object(api, "get", side_effect=lambda record_id: records[record_id]),
            patch.object(api, "impact", return_value={
                "relations": [], "affected_files": [],
                "dependency_files": [], "affected_tests": [],
            }),
            patch("project_knowledge.evaluate.rank_files", side_effect=record_rank),
        ):
            _retrieve(api, sample, "markdown")

        self.assertEqual(len(captured), 1)
        self.assertIn("src/app.py", captured[0])
        self.assertIn("src/helper.py", captured[0])

    def test_markdown_uses_all_structured_sources_from_task_selected_pages(self) -> None:
        for path in ("src/helper.py", "src/unselected.py"):
            (self.root / path).write_text("def helper(): return None\n", encoding="utf-8")
        ProjectService(self.root).sync()
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]
        selected = {
            "id": "curated.feature.account-login",
            "kind": "feature-guide",
            "score": 9.0,
            "summary": "AccountService login behavior.",
            "freshness": "fresh",
            "requires_live_source": False,
            "sources": [
                {"type": "file", "path": "src/app.py"},
                {"type": "file", "path": "src/helper.py"},
            ],
        }
        record = {
            **selected,
            "status": "fresh",
            "content": "AccountService login behavior without path citations.",
        }
        unselected = {
            "id": "curated.unrelated",
            "kind": "curated",
            "score": 0.1,
            "summary": "Unrelated notes.",
            "freshness": "fresh",
            "requires_live_source": False,
            "sources": [{"type": "file", "path": "src/unselected.py"}],
        }
        filler_pages = [
            {**selected, "id": f"curated.selected-{index}", "score": 8.0 - index}
            for index in range(2)
        ]
        captured = []
        real_rank = rank_files

        def record_rank(candidates, **kwargs):
            captured.extend(candidates)
            return real_rank(candidates, **kwargs)

        with (
            patch.object(
                api,
                "search",
                return_value={"results": [selected, *filler_pages, unselected]},
            ),
            patch.object(api, "get", return_value=record),
            patch.object(api, "impact", return_value={
                "relations": [], "affected_files": [],
                "dependency_files": [], "affected_tests": [],
            }),
            patch("project_knowledge.evaluate.rank_files", side_effect=record_rank),
        ):
            _retrieve(api, sample, "markdown")

        knowledge_candidates = {
            candidate.path: candidate
            for candidate in captured
            if "knowledge_source" in candidate.stages
        }
        self.assertTrue(knowledge_candidates["src/app.py"].direct_knowledge_source)
        self.assertTrue(knowledge_candidates["src/helper.py"].direct_knowledge_source)
        self.assertNotIn("src/unselected.py", knowledge_candidates)

    def test_grep_candidates_include_indexed_symbol_and_path_evidence(self) -> None:
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]

        result = _retrieve(api, sample, "grep_read")

        app_ranking = next(
            item for item in result["file_rankings"]
            if item["path"] == "src/app.py"
        )
        self.assertIn("exact_identity", app_ranking["why_selected"])
        self.assertIn("symbol_terms", app_ranking["why_selected"])
        self.assertEqual(result["core_files"][0], "src/app.py")

    def test_grep_content_eligibility_respects_production_source_role(self) -> None:
        (self.root / "src" / "repository_notes.py").write_text(
            "# AccountService login delegates to Repository save\n",
            encoding="utf-8",
        )
        (self.root / "test_repeated.py").write_text(
            "# AccountService login Repository save\n" * 20,
            encoding="utf-8",
        )
        ProjectService(self.root).sync()
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]
        captured = []
        real_rank = rank_files

        def record_rank(candidates, **kwargs):
            captured.extend(candidates)
            return real_rank(candidates, **kwargs)

        with patch("project_knowledge.evaluate.rank_files", side_effect=record_rank):
            result = _retrieve(api, sample, "grep_read")

        self.assertIn("src/repository_notes.py", result["files"])
        notes_ranking = next(
            item for item in result["file_rankings"]
            if item["path"] == "src/repository_notes.py"
        )
        self.assertIn("task_role_match", notes_ranking["why_selected"])
        self.assertIn("content_terms", notes_ranking["why_selected"])
        repeated_test = next(
            candidate for candidate in captured
            if candidate.path == "test_repeated.py"
        )
        self.assertFalse(repeated_test.task_role_match)

    def test_grep_uses_policy_v1_with_seven_file_limit(self) -> None:
        api = KnowledgeAPI(self.root)
        sample = load_dataset(self.dataset)[0]
        policies = []
        real_rank = rank_files

        def record_rank(candidates, **kwargs):
            policies.append(kwargs.get("policy"))
            return real_rank(candidates, **kwargs)

        with patch("project_knowledge.evaluate.rank_files", side_effect=record_rank):
            _retrieve(api, sample, "grep_read")

        self.assertEqual(len(policies), 1)
        self.assertIsNotNone(policies[0])
        self.assertEqual(policies[0].name, "policy-v1")
        self.assertEqual(policies[0].full_limit, 7)

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
