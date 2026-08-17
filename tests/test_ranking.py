from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from project_knowledge.ranking import (
    DEFAULT_RANKING_POLICY,
    FileCandidate,
    RankedFile,
    RankingPolicy,
    RankingResult,
    ScoreBreakdown,
    fallback_rank_files,
    rank_files,
    score_candidate,
)


class RankingTests(unittest.TestCase):
    def test_policy_v1_scores_each_category_once(self) -> None:
        candidate = FileCandidate(
            path="src/app.py",
            stages={"direct_symbol", "knowledge_source", "impact"},
            anchors={"src/app.py::AccountService.login"},
            exact_symbol=True,
            qualified_symbol=True,
            exact_filename=True,
            direct_knowledge_source=True,
            graph_hop=1,
            task_role_match=True,
            path_terms={"account", "login", "service", "ignored"},
            symbol_terms={"account", "login", "service", "ignored"},
            content_terms={"account", "login", "service", "repo", "extra"},
        )

        breakdown = score_candidate(candidate, DEFAULT_RANKING_POLICY)

        self.assertEqual(breakdown.identity, 100)
        self.assertEqual(breakdown.provenance, 35)
        self.assertEqual(breakdown.relation, 30)
        self.assertEqual(breakdown.role, 20)
        self.assertEqual(breakdown.text, 50)
        self.assertEqual(breakdown.penalties, 0)
        self.assertEqual(breakdown.total, 235)

    def test_irrelevant_test_and_fallback_only_penalties_are_explicit(self) -> None:
        candidate = FileCandidate(
            path="tests/test_unrelated.py",
            stages={"fallback"},
            is_test=True,
            path_terms={"login"},
            content_terms={"login"},
        )

        breakdown = score_candidate(candidate, DEFAULT_RANKING_POLICY)

        self.assertEqual(breakdown.penalties, -40)
        self.assertIn("irrelevant_test", breakdown.reasons)
        self.assertIn("fallback_only", breakdown.reasons)

    def test_unavailable_signals_are_sorted(self) -> None:
        candidate = FileCandidate(
            path="src/app.py",
            unavailable_signals={"symbol", "graph"},
        )

        breakdown = score_candidate(candidate, DEFAULT_RANKING_POLICY)

        self.assertEqual(breakdown.unavailable_signals, ("graph", "symbol"))

    def test_result_serializes_tuple_contracts_as_lists(self) -> None:
        breakdown = score_candidate(FileCandidate(path="src/app.py"), DEFAULT_RANKING_POLICY)
        ranking = RankedFile(
            path="src/app.py",
            tier="core",
            score=0,
            score_breakdown=breakdown,
            selection_stage="direct_symbol",
            why_selected="exact_identity",
        )
        result = RankingResult(
            core_files=("src/app.py",),
            supporting_files=(),
            files=("src/app.py",),
            file_rankings=(ranking,),
            withheld_files=(),
            rejected_files=(),
            ranking_policy="policy-v1",
            ranking_status="ok",
            ranking_confidence="high",
        )

        payload = result.to_dict()

        self.assertEqual(payload["core_files"], ["src/app.py"])
        self.assertEqual(payload["file_rankings"][0]["score_breakdown"]["reasons"], [])

    def test_external_ranking_contracts_are_frozen(self) -> None:
        breakdown = ScoreBreakdown(0, 0, 0, 0, 0, 0, 0, ())
        ranking = RankedFile(
            path="src/app.py",
            tier="core",
            score=0,
            score_breakdown=breakdown,
            selection_stage="direct_symbol",
            why_selected="exact_identity",
        )
        result = RankingResult(
            core_files=(),
            supporting_files=(),
            files=(),
            file_rankings=(),
            withheld_files=(),
            rejected_files=(),
            ranking_policy="policy-v1",
            ranking_status="ok",
            ranking_confidence="high",
        )

        for instance, field_name, value in (
            (RankingPolicy(), "name", "other"),
            (breakdown, "total", 1),
            (ranking, "tier", "supporting"),
            (result, "ranking_status", "fallback"),
        ):
            with self.subTest(contract=type(instance).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(instance, field_name, value)

    def test_requires_live_source_does_not_alter_score(self) -> None:
        base = FileCandidate(
            path="src/app.py",
            qualified_symbol=True,
            path_terms={"app"},
        )
        live_source = FileCandidate(
            path="src/app.py",
            qualified_symbol=True,
            path_terms={"app"},
            requires_live_source=True,
        )

        self.assertEqual(
            score_candidate(live_source, DEFAULT_RANKING_POLICY),
            score_candidate(base, DEFAULT_RANKING_POLICY),
        )

    def test_identity_precedence_is_exact_path_then_qualified_then_filename(self) -> None:
        cases = (
            ("exact_path", FileCandidate(path="src/app.py", exact_path=True, qualified_symbol=True, exact_filename=True), 100, "exact_identity"),
            ("qualified", FileCandidate(path="src/app.py", qualified_symbol=True, exact_filename=True), 70, "qualified_identity"),
            ("filename", FileCandidate(path="src/app.py", exact_filename=True), 40, "file_or_module_identity"),
        )

        for name, candidate, expected_score, expected_reason in cases:
            with self.subTest(identity=name):
                breakdown = score_candidate(candidate, DEFAULT_RANKING_POLICY)
                self.assertEqual(breakdown.identity, expected_score)
                self.assertEqual(breakdown.reasons, (expected_reason,))

    def test_affected_test_is_exempt_from_irrelevant_test_penalty(self) -> None:
        candidate = FileCandidate(
            path="tests/test_account.py",
            stages={"fallback"},
            is_test=True,
            affected_test=True,
        )

        breakdown = score_candidate(candidate, DEFAULT_RANKING_POLICY)

        self.assertEqual(breakdown.penalties, -15)
        self.assertNotIn("irrelevant_test", breakdown.reasons)
        self.assertEqual(breakdown.reasons, ("fallback_only",))

    def test_reasons_have_stable_policy_order(self) -> None:
        candidate = FileCandidate(
            path="src/account.py",
            exact_filename=True,
            direct_knowledge_source=True,
            graph_hop=2,
            task_role_match=True,
            path_terms={"account"},
            symbol_terms={"login"},
            content_terms={"service"},
        )
        penalty_candidate = FileCandidate(
            path="tests/test_unrelated.py",
            stages={"fallback"},
            is_test=True,
            path_terms={"login"},
            content_terms={"service"},
        )

        self.assertEqual(
            score_candidate(candidate, DEFAULT_RANKING_POLICY).reasons,
            (
                "file_or_module_identity",
                "direct_knowledge_source",
                "graph_hop_2",
                "task_role_match",
                "path_terms",
                "symbol_terms",
                "content_terms",
            ),
        )
        self.assertEqual(
            score_candidate(penalty_candidate, DEFAULT_RANKING_POLICY).reasons,
            ("path_terms", "content_terms", "irrelevant_test", "fallback_only"),
        )

    def test_rank_files_merges_duplicate_evidence_and_is_stable(self) -> None:
        candidates = [
            FileCandidate(path="src/z.py", stages={"fallback"}, content_terms={"login"}, original_order=0),
            FileCandidate(path="src/app.py", stages={"impact"}, graph_hop=1, original_order=1),
            FileCandidate(
                path="src/app.py",
                stages={"direct_symbol"},
                anchors={"src/app.py::login"},
                exact_symbol=True,
                original_order=2,
            ),
            FileCandidate(path="../outside.py", stages={"direct_symbol"}, exact_symbol=True, original_order=3),
        ]

        result = rank_files(candidates, allowed_paths={"src/app.py", "src/z.py"})

        self.assertEqual(result.core_files, ("src/app.py",))
        self.assertEqual(result.supporting_files, ())
        self.assertEqual(result.files, ("src/app.py",))
        self.assertEqual(result.file_rankings[0].score_breakdown.identity, 100)
        self.assertEqual(result.file_rankings[0].score_breakdown.relation, 30)
        self.assertEqual(result.file_rankings[0].selection_stage, "direct_symbol")
        self.assertEqual(result.rejected_files[0]["reason_code"], "path_not_allowed")

    def test_rank_files_caps_core_and_preserves_only_qualified_supporting(self) -> None:
        candidates = [
            FileCandidate(path=f"src/core_{index}.py", exact_symbol=True, stages={"direct_symbol"}, original_order=index)
            for index in range(6)
        ]
        candidates.extend([
            FileCandidate(path="src/support.py", graph_hop=2, stages={"impact"}, original_order=6),
            FileCandidate(path="src/weak.py", content_terms={"x"}, stages={"fallback"}, original_order=7),
        ])
        allowed = {candidate.path for candidate in candidates}

        result = rank_files(candidates, allowed_paths=allowed)

        self.assertEqual(len(result.core_files), 5)
        self.assertIn("src/core_5.py", result.supporting_files)
        self.assertIn("src/support.py", result.supporting_files)
        self.assertNotIn("src/weak.py", result.files)
        self.assertEqual(result.withheld_files[-1]["reason_code"], "below_supporting_threshold")

    def test_fallback_preserves_original_order_and_reports_reason(self) -> None:
        candidates = [
            FileCandidate(path="src/b.py", original_order=0),
            FileCandidate(path="src/a.py", original_order=1),
        ]

        result = fallback_rank_files(
            candidates,
            allowed_paths={"src/a.py", "src/b.py"},
            reason_code="ranking_error",
        )

        self.assertEqual(result.files, ("src/b.py", "src/a.py"))
        self.assertEqual(result.ranking_status, "fallback")
        self.assertEqual(result.reason_code, "ranking_error")

    def test_rank_files_caps_core_at_full_limit_for_inconsistent_policy(self) -> None:
        candidates = [
            FileCandidate(
                path=f"src/candidate_{index}.py",
                exact_symbol=True,
                stages={"direct_symbol"},
                original_order=index,
            )
            for index in range(15)
        ]
        policy = RankingPolicy(core_limit=15, full_limit=10)

        result = rank_files(
            candidates,
            allowed_paths={candidate.path for candidate in candidates},
            policy=policy,
        )

        self.assertEqual(len(result.core_files), 10)
        self.assertEqual(len(result.files), 10)

    def test_fallback_caps_core_at_full_limit_for_inconsistent_policy(self) -> None:
        candidates = [
            FileCandidate(path=f"src/candidate_{index}.py", original_order=index)
            for index in range(15)
        ]
        policy = RankingPolicy(core_limit=15, full_limit=10)

        result = fallback_rank_files(
            candidates,
            allowed_paths={candidate.path for candidate in candidates},
            reason_code="ranking_error",
            policy=policy,
        )

        self.assertEqual(len(result.core_files), 10)
        self.assertEqual(len(result.files), 10)


if __name__ == "__main__":
    unittest.main()
