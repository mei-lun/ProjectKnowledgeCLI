from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from project_knowledge.ranking import (
    DEFAULT_RANKING_POLICY,
    LEGACY_RANKING_POLICY,
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

        breakdown = score_candidate(candidate, LEGACY_RANKING_POLICY)

        self.assertEqual(breakdown.identity, 104)
        self.assertEqual(breakdown.provenance, 35)
        self.assertEqual(breakdown.relation, 30)
        self.assertEqual(breakdown.role, 20)
        self.assertEqual(breakdown.text, 50)
        self.assertEqual(breakdown.penalties, 0)
        self.assertEqual(breakdown.total, 239)

    def test_policy_v2_prefers_domain_definition_over_generic_vendor_hub(self) -> None:
        noise = FileCandidate(
            path="modules/skynet/lualib/socket.lua",
            stages={"direct_symbol"},
            channels={"symbol_exact", "graph_direct"},
            exact_symbol=True,
            generic_symbol=True,
            is_vendor=True,
            high_degree_hub=True,
            symbol_terms={"read"},
        )
        target = FileCandidate(
            path="src/app/game/magent/com/resident_order_com.lua",
            stages={"direct_symbol", "impact"},
            channels={"symbol_alias", "graph_direct", "lexical"},
            definition_match=True,
            specific_symbol=True,
            query_role_match=True,
            graph_hop=1,
            path_terms={"resident", "order"},
            symbol_terms={"get_order_strict"},
        )

        result = rank_files(
            [noise, target],
            allowed_paths={noise.path, target.path},
            query_type="invariant",
        )

        self.assertEqual(result.ranking_policy, "policy-v2")
        self.assertEqual(result.core_files[0], target.path)
        noise_score = score_candidate(noise, DEFAULT_RANKING_POLICY, query_type="invariant")
        self.assertIn("generic_symbol", noise_score.reasons)
        self.assertIn("vendor_source", noise_score.reasons)
        self.assertIn("high_degree_hub", noise_score.reasons)

    def test_policy_v2_only_boosts_tests_when_query_requests_tests(self) -> None:
        candidate = FileCandidate(
            path="src_dev/unittest/test_order.lua",
            stages={"impact"},
            channels={"test_config"},
            is_test=True,
            affected_test=True,
            query_role_match=True,
        )

        ordinary = score_candidate(candidate, DEFAULT_RANKING_POLICY, query_type="invariant")
        requested = score_candidate(candidate, DEFAULT_RANKING_POLICY, query_type="test_config")

        self.assertGreater(requested.total, ordinary.total)
        self.assertIn("requested_test", requested.reasons)

        generic = score_candidate(
            FileCandidate(path="tests/simulate.lua", is_test=True),
            DEFAULT_RANKING_POLICY,
            query_type="test_config",
        )
        self.assertIn("generic_test_noise", generic.reasons)
        self.assertLess(generic.total, ordinary.total)

    def test_policy_v2_demotes_exact_symbol_that_misses_extension_role(self) -> None:
        ordinary = FileCandidate(
            path="src/console.lua",
            exact_symbol=True,
            specific_symbol=True,
        )
        registry = FileCandidate(
            path="src/avatar/avatar_def.lua",
            definition_match=True,
            specific_symbol=True,
            query_role_match=True,
        )

        result = rank_files(
            [ordinary, registry],
            allowed_paths={ordinary.path, registry.path},
            query_type="extension_point",
        )

        self.assertEqual(result.core_files[0], registry.path)
        ordinary_score = score_candidate(
            ordinary,
            DEFAULT_RANKING_POLICY,
            query_type="extension_point",
        )
        self.assertIn("query_profile_mismatch", ordinary_score.reasons)

    def test_legacy_policy_remains_available_for_rollback(self) -> None:
        candidate = FileCandidate(path="src/app.py", exact_symbol=True)
        result = rank_files(
            [candidate],
            allowed_paths={candidate.path},
            policy=LEGACY_RANKING_POLICY,
        )
        self.assertEqual(result.ranking_policy, "policy-v1")

    def test_test_query_core_keeps_both_source_and_test_roles(self) -> None:
        sources = [
            FileCandidate(
                path=f"src/source_{index}.py",
                exact_symbol=True,
                query_role_match=True,
            )
            for index in range(5)
        ]
        test = FileCandidate(
            path="tests/test_feature.py",
            is_test=True,
            affected_test=True,
            query_role_match=True,
            graph_hop=1,
        )

        result = rank_files(
            [*sources, test],
            allowed_paths={item.path for item in [*sources, test]},
            query_type="test_config",
        )

        self.assertIn(test.path, result.core_files)
        self.assertTrue(any(path.startswith("src/") for path in result.core_files))

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
        self.assertEqual(payload["optional_files"], [])
        self.assertEqual(payload["file_rankings"][0]["score_breakdown"]["reasons"], [])

    def test_rank_files_publishes_stable_optional_tier_without_expanding_files(self) -> None:
        candidates = [
            FileCandidate(
                path=f"src/core_{index}.py",
                exact_symbol=True,
                stages={"direct_symbol"},
                original_order=index,
            )
            for index in range(5)
        ]
        candidates.extend(
            [
                FileCandidate(path="src/support.py", graph_hop=1, stages={"impact"}, original_order=5),
                FileCandidate(path="src/optional.py", content_terms={"login"}, stages={"impact"}, original_order=6),
                FileCandidate(path="src/optional_b.py", content_terms={"login"}, stages={"impact"}, original_order=7),
                FileCandidate(path="src/withheld.py", original_order=8),
            ]
        )

        result = rank_files(
            candidates,
            allowed_paths={candidate.path for candidate in candidates},
        )

        self.assertEqual(len(result.core_files), 5)
        self.assertEqual(result.supporting_files, ("src/support.py",))
        self.assertEqual(result.optional_files, ("src/optional.py", "src/optional_b.py"))
        self.assertEqual(result.files, result.core_files + result.supporting_files)
        self.assertEqual(
            [item.tier for item in result.file_rankings],
            ["core"] * 5 + ["supporting", "optional", "optional"],
        )
        self.assertNotIn("src/optional.py", {item["path"] for item in result.withheld_files})
        self.assertEqual(result.withheld_files[-1]["reason_code"], "below_supporting_threshold")

    def test_optional_tier_is_bounded_and_preserves_fallback_order(self) -> None:
        candidates = [
            FileCandidate(path=f"src/file_{index}.py", original_order=index)
            for index in range(14)
        ]
        policy = RankingPolicy(optional_limit=2)

        result = fallback_rank_files(
            candidates,
            allowed_paths={candidate.path for candidate in candidates},
            reason_code="ranking_error",
            policy=policy,
        )

        self.assertEqual(result.files, tuple(f"src/file_{index}.py" for index in range(10)))
        self.assertEqual(result.optional_files, ("src/file_10.py", "src/file_11.py"))
        self.assertEqual(result.file_rankings[-2].tier, "optional")
        self.assertEqual(result.file_rankings[-1].tier, "optional")
        self.assertEqual(
            [item["path"] for item in result.withheld_files],
            ["src/file_12.py", "src/file_13.py"],
        )

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
            ("exact_path", FileCandidate(path="src/app.py", exact_path=True, qualified_symbol=True, exact_filename=True), 104, "exact_identity"),
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

    def test_exact_source_survives_five_affected_test_matches(self) -> None:
        source = FileCandidate(
            path="src/project_knowledge/ranking.py",
            stages={"direct_symbol", "impact"},
            exact_symbol=True,
            graph_hop=1,
            symbol_terms={"rank_files"},
        )
        affected_tests = [
            FileCandidate(
                path=f"tests/test_noise_{index}.py",
                stages={"knowledge_source", "impact"},
                exact_filename=True,
                direct_knowledge_source=True,
                graph_hop=1,
                task_role_match=True,
                path_terms={"ranking"},
                content_terms={"rank", "files", "policy"},
                is_test=True,
                affected_test=True,
            )
            for index in range(5)
        ]

        result = rank_files(
            [source, *affected_tests],
            allowed_paths={source.path, *(item.path for item in affected_tests)},
        )

        self.assertIn(source.path, result.core_files)

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
            score_candidate(candidate, LEGACY_RANKING_POLICY).reasons,
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
            score_candidate(penalty_candidate, LEGACY_RANKING_POLICY).reasons,
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
        self.assertEqual(result.file_rankings[0].score_breakdown.identity, 104)
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
