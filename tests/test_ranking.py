from __future__ import annotations

import unittest

from project_knowledge.ranking import (
    DEFAULT_RANKING_POLICY,
    FileCandidate,
    RankedFile,
    RankingResult,
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


if __name__ == "__main__":
    unittest.main()
