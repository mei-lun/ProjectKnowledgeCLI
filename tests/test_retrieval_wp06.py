from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_knowledge.codegraph import CodeGraphEngine
from project_knowledge.config import ProjectConfig
from project_knowledge.models import Relation
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.service import ProjectService
from project_knowledge.store import KnowledgeStore
from project_knowledge.util import approx_tokens


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
        self.assertIn("selected_records", explanation)
        self.assertTrue(explanation["reference_implementations"])
        self.assertTrue(any("create_item" in item["symbol"] for item in explanation["reference_implementations"]))
        self.assertIn("extension_points", explanation)
        self.assertIn("unknowns", explanation)

    def test_context_returns_ranked_core_and_supporting_files(self) -> None:
        result = self.api.context("新增类似功能 create_item", max_tokens=1200)

        self.assertEqual(result["ranking_status"], "ok")
        self.assertEqual(result["ranking_policy"], "policy-v2")
        self.assertLessEqual(len(result["core_files"]), 5)
        self.assertLessEqual(len(result["files"]), 10)
        self.assertEqual(
            result["files"],
            result["core_files"] + result["supporting_files"],
        )
        self.assertEqual(result["core_files"][0], "src/app.py")
        self.assertEqual(
            [item["path"] for item in result["file_rankings"]],
            result["files"],
        )
        self.assertTrue(all(item["why_selected"] for item in result["file_rankings"]))

    def test_context_publishes_runtime_required_evidence_without_oracle_input(self) -> None:
        result = self.api.context("create_item 的调用链", max_tokens=1800)

        self.assertIn("pre_required_evidence", result)
        self.assertIn("post_required_evidence", result)
        self.assertIn("required_evidence", result)
        symbol_ids = {
            item["id"] for item in result["pre_required_evidence"]["symbols"]
        }
        self.assertIn("src/app.py::create_item", symbol_ids)
        paths = result["pre_required_evidence"]["relation_paths"]
        if paths:
            self.assertTrue(all(edge["kind"] == "calls" for edge in paths[0]["edges"]))
        self.assertEqual(result["post_required_evidence"], result["required_evidence"])
        self.assertFalse(result["context_incomplete"])
        self.assertEqual(result["missing_required_evidence"], [])
        self.assertEqual(result["budget_status"], "within_budget")

    def test_context_does_not_mark_ambiguous_symbol_as_required(self) -> None:
        result = self.api.context("处理一个问题", max_tokens=1200)

        self.assertEqual(result["pre_required_evidence"]["symbols"], [])
        self.assertEqual(result["pre_required_evidence"]["relation_paths"], [])

    def test_context_status_marks_unanchored_query_as_needs_source_check(self) -> None:
        result = self.api.context("处理一个未命名的动态扩展问题", max_tokens=1200)

        self.assertEqual(result["context_status"]["state"], "needs_source_check")
        self.assertTrue(result["context_status"]["needs_source_check"])
        self.assertIn("no_exact_symbol_anchor", result["context_status"]["reasons"])
        self.assertNotEqual(result["context_status"]["state"], "complete")

    def test_context_status_marks_ranking_fallback_as_low_confidence(self) -> None:
        with patch("project_knowledge.retrieval.rank_files", side_effect=RuntimeError("ranking unavailable")):
            result = self.api.context("create_item", max_tokens=1200)

        self.assertEqual(result["context_status"]["state"], "low_confidence")
        self.assertEqual(result["context_status"]["confidence"], "low")
        self.assertIn("ranking_fallback", result["context_status"]["reasons"])

    def test_debug_trace_marks_ranking_fallback_partial(self) -> None:
        with patch("project_knowledge.retrieval.rank_files", side_effect=RuntimeError("ranking unavailable")):
            result = self.api.context("create_item", max_tokens=1200, debug=True)

        self.assertEqual(result["retrieval_trace"]["stage_timings"]["ranking"]["status"], "partial")
        self.assertEqual(result["context_status"]["state"], "low_confidence")

    def test_context_status_prioritizes_incomplete_budget_over_other_states(self) -> None:
        result = self.api.context("create_item 的调用链", max_tokens=256)

        self.assertEqual(result["context_status"]["state"], "context_incomplete")
        self.assertTrue(result["context_status"]["needs_source_check"])
        self.assertIn("required_evidence_missing", result["context_status"]["reasons"])

    def test_debug_trace_v2_contains_stage_timings_and_budget_state(self) -> None:
        result = self.api.context("create_item 的调用链", max_tokens=1200, debug=True)

        trace = result["retrieval_trace"]
        self.assertEqual(trace["schema_version"], 2)
        self.assertIn("stage_timings", trace)
        for stage in ("lexical", "codegraph", "ranking", "context_assembly"):
            self.assertIn(stage, trace["stage_timings"])
            self.assertGreaterEqual(trace["stage_timings"][stage]["duration_ms"], 0)
            self.assertIn(trace["stage_timings"][stage]["status"], {"ok", "partial", "error"})
        self.assertIn("codegraph", trace["stages"])
        self.assertIn("evidence", trace["stages"]["context_assembly"])
        self.assertIn("pre", trace["stages"]["context_assembly"]["evidence"])
        self.assertIn("post", trace["stages"]["context_assembly"]["evidence"])
        self.assertIn("trim_events", trace["stages"]["context_assembly"])
        self.assertIn("context_status", trace)

        if trace["stages"]["context_assembly"]["trim_events"]:
            event = trace["stages"]["context_assembly"]["trim_events"][0]
            self.assertIn("action", event)
            self.assertIn("reason_code", event)
            self.assertIn("tokens_before", event)
            self.assertIn("tokens_after", event)

    def test_context_does_not_emit_trace_by_default(self) -> None:
        self.assertNotIn("retrieval_trace", self.api.context("create_item", max_tokens=1200))

    def test_debug_trace_respects_context_budget(self) -> None:
        result = self.api.context("create_item 的调用链", max_tokens=256, debug=True)

        self.assertLessEqual(result["estimated_tokens"], 256)
        self.assertLessEqual(approx_tokens(json.dumps(result, ensure_ascii=False)), 256)
        self.assertIn("status", result["retrieval_trace"])
        self.assertIn("duration_ms", result["retrieval_trace"])

    def test_context_returns_optional_files_as_a_separate_non_context_tier(self) -> None:
        result = self.api.context("ranking.py policy", max_tokens=1800)

        self.assertIn("optional_files", result)
        self.assertEqual(result["files"], result["core_files"] + result["supporting_files"])
        self.assertTrue(
            all(item["tier"] == "optional" for item in result["file_rankings"] if item["path"] in result["optional_files"])
        )
        self.assertTrue(set(result["optional_files"]).isdisjoint(result["files"]))

    def test_unrelated_test_file_does_not_displace_exact_source(self) -> None:
        (self.root / "tests" / "test_noise.py").write_text(
            "def create_item():\n    return 'noise'\n" * 50,
            encoding="utf-8",
        )
        ProjectService(self.root).sync()

        result = KnowledgeAPI(self.root).context("create_item", max_tokens=1200)

        self.assertEqual(result["core_files"][0], "src/app.py")
        self.assertNotEqual(result["core_files"][0], "tests/test_noise.py")

    def test_context_ranking_failure_is_structured_and_compatible(self) -> None:
        with patch(
            "project_knowledge.retrieval.rank_files",
            side_effect=RuntimeError("private C:\\secret"),
        ):
            result = self.api.context("create_item", max_tokens=1200)

        self.assertEqual(result["ranking_status"], "fallback")
        self.assertEqual(result["ranking_reason_code"], "ranking_error")
        self.assertNotIn("secret", json.dumps(result, ensure_ascii=False))
        self.assertIn("symbols", result)
        self.assertIn("knowledge", result)
        self.assertIn("impact", result)

    def test_fit_context_keeps_token_budget_withholding_through_legacy_trimming(self) -> None:
        result = {
            "symbols": [{"name": "create_item"}],
            "impact": {
                "affected_files": ["src/app.py"],
                "affected_tests": [],
                "affected_knowledge": [],
                "affected_modules": ["src"],
            },
            "reference_implementations": [],
            "extension_points": [],
            "retrieval_explanation": {"selected_records": [], "impact": {}},
            "core_files": ["src/app.py"],
            "supporting_files": ["tests/test_app.py"],
            "files": ["src/app.py", "tests/test_app.py"],
            "file_rankings": [
                {"path": "src/app.py", "why_selected": "exact_identity"},
                {"path": "tests/test_app.py", "why_selected": "affected_test"},
            ],
            "withheld_files": [],
            "rejected_files": [],
            "knowledge": [{"content": "evidence " * 800, "tokens": 800}],
            "gaps": ["Inspect source."],
            "summary": "Context summary.",
            "estimated_tokens": 0,
        }

        KnowledgeAPI._fit_context(result, budget=375)

        self.assertEqual(result["supporting_files"], [])
        self.assertLess(result["knowledge"][0]["tokens"], 800)
        self.assertIn(
            {"path": "tests/test_app.py", "reason_code": "token_budget"},
            result["withheld_files"],
        )

    def test_fit_context_atomically_preserves_required_symbol_and_ordered_path(self) -> None:
        required = {
            "symbols": [{
                "id": "src/app.py::create_item",
                "path": "src/app.py",
                "signature": "def create_item(value)",
                "span": {"start": 6, "end": 7},
            }],
            "relation_paths": [{"edges": [
                {"source": "src/app.py::create_item", "kind": "calls", "target": "src/app.py::Repository.save"},
            ]}],
        }
        result = {
            "task": "create_item 调用链",
            "symbols": [
                {"id": "src/app.py::create_item", "signature": "def create_item(value)", "span": {"start": 6, "end": 7}},
                *[{"id": f"noise::{index}"} for index in range(20)],
            ],
            "impact": {"affected_files": [], "affected_tests": [], "affected_knowledge": [], "affected_modules": [], "call_path": []},
            "reference_implementations": [], "extension_points": [],
            "retrieval_explanation": {"selected_records": [], "impact": {}},
            "core_files": ["src/app.py"], "supporting_files": ["tests/test_app.py"],
            "optional_files": ["docs/noise.md"],
            "files": ["src/app.py", "tests/test_app.py"],
            "file_rankings": [
                {"path": "src/app.py", "tier": "core", "score": 100},
                {"path": "tests/test_app.py", "tier": "supporting", "score": 1},
                {
                    "path": "docs/noise.md", "tier": "optional", "score": 0,
                    "score_breakdown": {"noise": "detail " * 600},
                },
            ],
            "withheld_files": [], "rejected_files": [],
            "knowledge": [{"content": "noise " * 50, "tokens": 50, "sources": [{"path": "tests/test_app.py"}]}],
            "gaps": [], "summary": "summary", "estimated_tokens": 0,
            "pre_required_evidence": required,
            "post_required_evidence": required,
            "required_evidence": required,
            "context_incomplete": False, "missing_required_evidence": [],
            "budget_status": "pending", "minimum_required_tokens": 0,
        }

        KnowledgeAPI._fit_context(result, budget=1100)

        self.assertEqual(result["post_required_evidence"], required)
        self.assertEqual(result["required_evidence"], required)
        self.assertFalse(result["context_incomplete"])
        self.assertEqual(result["optional_files"], [])
        self.assertEqual(result["supporting_files"], ["tests/test_app.py"])
        self.assertLessEqual(result["estimated_tokens"], 1100)

    def test_fit_context_reports_missing_atomic_evidence_when_budget_is_impossible(self) -> None:
        path = {"edges": [
            {"source": "symbol:A", "kind": "calls", "target": "symbol:B"},
            {"source": "symbol:B", "kind": "calls", "target": "symbol:C"},
        ]}
        required = {
            "symbols": [{"id": "symbol:A", "signature": "def a(value)", "span": {"start": 1, "end": 2}}],
            "relation_paths": [path],
        }
        result = {
            "task": "A call path", "symbols": [{"id": "symbol:A"}],
            "impact": {"affected_files": [], "affected_tests": [], "affected_knowledge": [], "affected_modules": [], "call_path": []},
            "reference_implementations": [], "extension_points": [],
            "retrieval_explanation": {"selected_records": [], "impact": {}},
            "core_files": ["src/a.py"], "supporting_files": [], "optional_files": [],
            "files": ["src/a.py"], "file_rankings": [], "withheld_files": [], "rejected_files": [],
            "knowledge": [], "gaps": [], "summary": "summary", "estimated_tokens": 0,
            "pre_required_evidence": required, "post_required_evidence": required,
            "required_evidence": required, "context_incomplete": False,
            "missing_required_evidence": [], "budget_status": "pending", "minimum_required_tokens": 0,
        }

        KnowledgeAPI._fit_context(result, budget=256)

        self.assertLessEqual(result["estimated_tokens"], 256)
        self.assertTrue(result["context_incomplete"])
        self.assertEqual(result["budget_status"], "insufficient_for_required")
        self.assertGreater(result["minimum_required_tokens"], 256)
        self.assertTrue(result["missing_required_evidence"])
        self.assertNotEqual(result["post_required_evidence"], required)
        self.assertNotIn(path, result["post_required_evidence"]["relation_paths"])

    def test_fit_context_hard_limits_long_impossible_evidence_identifiers(self) -> None:
        long_id = "src/" + "nested/" * 40 + "module.py::Qualified.symbol"
        required = {
            "symbols": [{"id": long_id, "signature": "def symbol(value)", "span": {"start": 1, "end": 2}}],
            "relation_paths": [{"edges": [
                {"source": long_id, "kind": "calls", "target": long_id + ".target"},
            ]}],
        }
        result = {
            "task": long_id, "symbols": [{"id": long_id}],
            "impact": {"affected_files": [], "affected_tests": [], "affected_knowledge": [], "affected_modules": [], "call_path": []},
            "reference_implementations": [], "extension_points": [],
            "retrieval_explanation": {"selected_records": [], "impact": {}},
            "core_files": [long_id], "supporting_files": [], "optional_files": [],
            "files": [long_id], "file_rankings": [], "withheld_files": [], "rejected_files": [],
            "knowledge": [], "gaps": [], "summary": "", "estimated_tokens": 0,
            "pre_required_evidence": required, "post_required_evidence": required,
            "required_evidence": required, "context_incomplete": False,
            "missing_required_evidence": [], "budget_status": "pending", "minimum_required_tokens": 0,
        }

        KnowledgeAPI._fit_context(result, budget=256)

        self.assertLessEqual(result["estimated_tokens"], 256)
        self.assertTrue(result["context_incomplete"])
        self.assertEqual(result["budget_status"], "insufficient_for_required")
        self.assertGreater(result["minimum_required_tokens"], 256)

    def test_runtime_relation_candidates_join_adjacent_edges_into_one_path(self) -> None:
        symbols = [
            {"id": "src/a.py::A", "path": "src/a.py", "name": "A"},
            {"id": "src/b.py::B", "path": "src/b.py", "name": "B"},
            {"id": "src/c.py::C", "path": "src/c.py", "name": "C"},
        ]
        traces = {
            "src/a.py::A": [Relation("src/a.py::A", "src/b.py::B", "calls", "src/b.py", 2, 1.0, True)],
            "src/b.py::B": [Relation("src/b.py::B", "src/c.py::C", "calls", "src/c.py", 3, 1.0, True)],
            "src/c.py::C": [],
        }
        with patch.object(self.api.service.engine, "trace", side_effect=lambda _root, symbol_id, _config, **_kwargs: traces[symbol_id]):
            relations = self.api._required_relation_candidates("call_path", symbols)

        self.assertEqual([item["order"] for item in relations], [1, 2])
        self.assertEqual(len({item["path_id"] for item in relations}), 1)

    def test_fit_context_preserves_exact_evidence_before_ranking_diagnostics(self) -> None:
        result = {
            "symbols": [{
                "id": "src/app.py::create_item",
                "name": "create_item",
                "path": "src/app.py",
            }],
            "impact": {
                "affected_files": [],
                "affected_tests": [],
                "affected_knowledge": [],
                "affected_modules": ["src"],
                "call_path": [],
            },
            "reference_implementations": [
                {"symbol": f"src/noise.py::symbol_{index}", "path": "src/noise.py"}
                for index in range(4)
            ],
            "extension_points": [
                {"symbol": f"src/noise.py::extension_{index}", "path": "src/noise.py"}
                for index in range(4)
            ],
            "retrieval_explanation": {
                "selected_records": [
                    {"id": f"generated.noise.{index}", "why_selected": "text match"}
                    for index in range(4)
                ],
                "reference_implementations": [
                    {"symbol": f"src/noise.py::symbol_{index}", "path": "src/noise.py"}
                    for index in range(4)
                ],
                "extension_points": [
                    {"symbol": f"src/noise.py::extension_{index}", "path": "src/noise.py"}
                    for index in range(4)
                ],
                "unknowns": ["dynamic dispatch requires live verification"],
                "impact": {
                    "files": [f"src/noise_{index}.py" for index in range(8)],
                    "tests": [f"tests/noise_{index}.py" for index in range(8)],
                },
            },
            "core_files": ["src/app.py", "src/a.py", "src/b.py", "src/c.py", "src/d.py"],
            "supporting_files": [],
            "files": ["src/app.py", "src/a.py", "src/b.py", "src/c.py", "src/d.py"],
            "file_rankings": [
                {"path": path, "tier": "core", "why_selected": "exact_identity"}
                for path in ("src/app.py", "src/a.py", "src/b.py", "src/c.py", "src/d.py")
            ],
            "withheld_files": [
                {"path": "tests/test_app.py", "reason_code": "token_budget"},
                *[
                    {"path": f"tests/noise_{index}.py", "reason_code": "selection_limit"}
                    for index in range(30)
                ],
            ],
            "rejected_files": [
                {"path": f"outside_{index}.py", "reason_code": "path_not_allowed"}
                for index in range(10)
            ],
            "knowledge": [{
                "id": "verified.create-item",
                "content": "create_item is implemented in src/app.py.",
                "sources": [
                    {"type": "file", "path": f"tests/noise_{index}.py"}
                    for index in range(30)
                ],
                "tokens": 12,
            }],
            "gaps": [],
            "unknowns": ["dynamic dispatch requires live verification"],
            "likely_modules": ["src"],
            "verification_commands": ["python -m pytest"],
            "guidance_workflow": {
                "available": False,
                "coverage": {"covered_files": 0, "total_files": 0, "complete": False},
                "categories": {"total": 0, "with_formal_guidance": 0},
                "pending_drafts": [],
                "pending_changes": [],
            },
            "ranking_policy": "policy-v1",
            "ranking_status": "ok",
            "ranking_confidence": "high",
            "summary": "create_item implementation context.",
            "estimated_tokens": 0,
        }

        KnowledgeAPI._fit_context(result, budget=350)

        self.assertIn(
            {"path": "tests/test_app.py", "reason_code": "token_budget"},
            result["withheld_files"],
        )
        self.assertEqual(result["symbols"][0]["id"], "src/app.py::create_item")
        self.assertIn("src/app.py", result["knowledge"][0]["content"])
        self.assertLessEqual(result["estimated_tokens"], 375)
        self.assertFalse(result["rejected_files"])
        self.assertTrue(all(
            item["reason_code"] == "token_budget"
            for item in result["withheld_files"]
        ))

    def test_fit_context_withholds_supporting_before_compacting_ranking_details(self) -> None:
        result = {
            "symbols": [{"id": "src/app.py::create_item", "name": "create_item"}],
            "impact": {
                "affected_files": [], "affected_tests": [],
                "affected_knowledge": [], "affected_modules": [],
            },
            "reference_implementations": [],
            "extension_points": [],
            "retrieval_explanation": {"selected_records": [], "impact": {}},
            "core_files": ["src/app.py"],
            "supporting_files": ["tests/test_app.py"],
            "files": ["src/app.py", "tests/test_app.py"],
            "file_rankings": [
                {
                    "path": path,
                    "tier": tier,
                    "why_selected": reason,
                    "score_breakdown": {"diagnostic": "detail " * 160},
                }
                for path, tier, reason in (
                    ("src/app.py", "core", "exact_identity"),
                    ("tests/test_app.py", "supporting", "graph_hop_1"),
                )
            ],
            "withheld_files": [],
            "rejected_files": [],
            "knowledge": [{
                "id": "verified.create-item",
                "content": "create_item is implemented in src/app.py.",
                "sources": [{"type": "file", "path": "src/app.py"}],
                "tokens": 12,
            }],
            "gaps": [],
            "unknowns": [],
            "likely_modules": [],
            "verification_commands": [],
            "guidance_workflow": {"available": False},
            "summary": "create_item implementation context.",
            "estimated_tokens": 0,
        }

        KnowledgeAPI._fit_context(result, budget=375)

        self.assertEqual(result["supporting_files"], [])
        self.assertTrue(any(
            item.get("path") == "tests/test_app.py"
            and item.get("reason_code") == "token_budget"
            for item in result["withheld_files"]
        ))
        self.assertTrue(all(
            "score_breakdown" not in item for item in result["file_rankings"]
        ))
        self.assertLessEqual(result["estimated_tokens"], 375)

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

    def test_relevant_excerpt_does_not_let_generic_rules_hide_task_evidence(self) -> None:
        invariant = "同一批修改或新增内容只递增一次补丁版本。"
        generic_rules = "\n".join(
            f"无关约束 {index}：发布前必须验证普通流程。" for index in range(80)
        )

        excerpt = KnowledgeAPI._relevant_excerpt(
            generic_rules + "\n" + invariant,
            "bump_patch_version 如何递增补丁版本？",
            budget=120,
        )

        self.assertIn(invariant, excerpt)

    def test_search_exposes_score_breakdown_and_impact_supports_bounded_multihop(self) -> None:
        results = self.api.search("app.py")
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

    def test_context_candidates_propagate_relation_hop_to_target_file(self) -> None:
        candidates, _ = self.api._context_file_candidates(
            "token budget",
            {"task_type": "investigation"},
            [],
            {
                "relations": [{
                    "source": "tests/test_app.py::<module>",
                    "target": "src/app.py::create_item",
                    "path": "tests/test_app.py",
                    "hop": 2,
                }],
                "affected_files": ["src/app.py"],
                "dependency_files": ["src/app.py"],
                "affected_tests": [],
            },
            [],
        )

        app_candidates = [
            candidate for candidate in candidates
            if candidate.path == "src/app.py" and "impact" in candidate.stages
        ]
        self.assertTrue(app_candidates)
        self.assertTrue(all(candidate.graph_hop == 2 for candidate in app_candidates))

    def test_context_only_marks_excerpt_citations_as_direct_sources(self) -> None:
        candidates, _ = self.api._context_file_candidates(
            "repository persistence",
            {"task_type": "investigation"},
            [],
            {"relations": [], "affected_files": [], "affected_tests": []},
            [{
                "id": "generated.module.sample",
                "freshness": "fresh",
                "requires_live_source": False,
                "content": "The implementation is `src/app.py::Repository.save`.",
                "sources": [
                    {"path": "src/app.py", "id": "src/app.py::Repository.save"},
                    {"path": "tests/test_app.py"},
                ],
            }],
        )

        direct_by_path = {
            candidate.path: candidate.direct_knowledge_source
            for candidate in candidates
            if "knowledge_source" in candidate.stages
        }
        self.assertTrue(direct_by_path["src/app.py"])
        self.assertFalse(direct_by_path["tests/test_app.py"])

    def test_context_bounds_module_tests_and_keeps_task_relevant_test(self) -> None:
        noise_paths = []
        for index in range(8):
            path = f"tests/test_noise_{index}.py"
            (self.root / path).write_text("def test_noise(): pass\n", encoding="utf-8")
            noise_paths.append(path)
        ProjectService(self.root).sync()

        candidates, _ = self.api._context_file_candidates(
            "impact test_app.py",
            {"task_type": "impact_analysis"},
            [],
            {
                "relations": [],
                "affected_files": [],
                "affected_tests": noise_paths + ["tests/test_app.py"],
            },
            [],
        )

        affected_tests = [
            candidate for candidate in candidates if candidate.affected_test
        ]
        self.assertLessEqual(len(affected_tests), 4)
        self.assertIn("tests/test_app.py", {candidate.path for candidate in affected_tests})

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
        client.snapshot.return_value = {
            "snapshot_id": "mock-snapshot",
            "files": [
                {"path": "src/app.lua", "language": "lua"},
                {"path": "src/router.lua", "language": "lua"},
            ],
        }
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

    def test_impact_uses_codegraph_public_response_for_dependencies(self) -> None:
        (self.root / "src" / "helper.py").write_text(
            "def persist(value):\n    return value\n", encoding="utf-8"
        )
        (self.root / "src" / "app.py").write_text(
            "from src.helper import persist\n\ndef create_item(value):\n    return persist(value)\n",
            encoding="utf-8",
        )
        (self.root / "a.py").write_text(
            "from src.app import create_item\n\ndef caller():\n    return create_item(1)\n",
            encoding="utf-8",
        )
        ProjectService(self.root).sync()
        api = KnowledgeAPI(self.root)

        result = api.impact(
            symbols=["src/app.py::create_item"], max_hops=1, max_relations=1
        )

        self.assertEqual(result["relations"][0]["source"], "src/app.py::create_item")
        self.assertIn("src/app.py", result["affected_files"])
        self.assertEqual(result["fact_source"], "codegraph")


if __name__ == "__main__":
    unittest.main()
