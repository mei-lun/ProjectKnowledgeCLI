from __future__ import annotations

import unittest

from project_knowledge.context_evidence import RequiredEvidencePlanner


class RequiredEvidencePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = RequiredEvidencePlanner()

    def test_exact_symbol_anchor_is_required_with_minimal_source_payload(self) -> None:
        result = self.planner.plan(
            task="请检查 src/app.py::login 的调用链",
            query_profile="call_path",
            core_file_paths=["src/app.py", "src/auth.py"],
            symbols=[
                {
                    "id": "src/app.py::login",
                    "qualified_name": "app.login",
                    "name": "login",
                    "path": "src/app.py",
                    "signature": "def login(user)",
                    "span": {"start": 10, "end": 20},
                    "freshness": "fresh",
                    "confidence": 1.0,
                    "match_reason": "exact_symbol",
                }
            ],
            relations=[],
        )

        self.assertEqual(len(result["required_symbols"]), 1)
        evidence = result["required_symbols"][0]
        self.assertEqual(evidence["evidence_id"], "symbol:src/app.py::login")
        self.assertEqual(evidence["retention"], "required")
        self.assertEqual(evidence["reason_code"], "exact_symbol_anchor")
        self.assertEqual(
            evidence["payload"],
            {
                "symbol_id": "src/app.py::login",
                "path": "src/app.py",
                "qualified_name": "app.login",
                "signature": "def login(user)",
                "span": {"start": 10, "end": 20},
            },
        )

    def test_resolved_ordered_relation_path_is_atomic_and_stable(self) -> None:
        symbols = [
            {"id": "src/app.py::login", "name": "login", "path": "src/app.py", "freshness": "fresh"},
            {"id": "src/auth.py::authenticate", "name": "authenticate", "path": "src/auth.py", "freshness": "fresh"},
            {"id": "src/db.py::load_user", "name": "load_user", "path": "src/db.py", "freshness": "fresh"},
        ]
        relations = [
            {
                "source": "src/auth.py::authenticate",
                "target": "src/db.py::load_user",
                "kind": "calls",
                "direction": "outgoing",
                "order": 2,
                "path_id": "login-flow",
                "resolved": True,
                "freshness": "fresh",
            },
            {
                "source": "src/app.py::login",
                "target": "src/auth.py::authenticate",
                "kind": "calls",
                "direction": "outgoing",
                "order": 1,
                "path_id": "login-flow",
                "resolved": True,
                "freshness": "fresh",
            },
        ]
        first = self.planner.plan(
            task="src/app.py::login 调用链",
            query_profile="call_path",
            core_file_paths=["src/app.py", "src/auth.py", "src/db.py"],
            symbols=symbols,
            relations=relations,
        )
        second = self.planner.plan(
            task="src/app.py::login 调用链",
            query_profile="call_path",
            core_file_paths=["src/app.py", "src/auth.py", "src/db.py"],
            symbols=symbols,
            relations=list(reversed(relations)),
        )

        paths = first["required_relation_paths"]
        self.assertEqual(len(paths), 1)
        self.assertEqual([edge["order"] for edge in paths[0]["payload"]["edges"]], [1, 2])
        self.assertEqual(paths[0]["retention"], "required")
        self.assertEqual(paths[0]["reason_code"], "exact_relation_path")
        self.assertEqual(paths[0]["evidence_id"], paths[0]["payload"]["path_id"])
        self.assertEqual(paths, second["required_relation_paths"])

    def test_ambiguous_stale_or_non_core_evidence_is_not_required(self) -> None:
        result = self.planner.plan(
            task="处理登录问题",
            query_profile="impact",
            core_file_paths=["src/app.py"],
            symbols=[
                {"id": "src/app.py::login", "name": "login", "path": "src/app.py", "confidence": 0.8},
                {
                    "id": "src/app.py::stale_login",
                    "name": "stale_login",
                    "path": "src/app.py",
                    "confidence": 1.0,
                    "match_reason": "exact_symbol",
                    "freshness": "stale",
                },
                {
                    "id": "src/other.py::login",
                    "name": "login",
                    "path": "src/other.py",
                    "confidence": 1.0,
                    "match_reason": "exact_symbol",
                    "freshness": "fresh",
                },
            ],
            relations=[
                {
                    "source": "src/app.py::login",
                    "target": "src/other.py::handler",
                    "kind": "calls",
                    "resolved": False,
                    "freshness": "fresh",
                }
            ],
        )

        self.assertEqual(result["required_symbols"], [])
        self.assertEqual(result["required_relation_paths"], [])

    def test_alias_name_match_is_not_promoted_to_required(self) -> None:
        result = self.planner.plan(
            task="AccountApi.login 调用链",
            query_profile="call_path",
            core_file_paths=["src/client.lua"],
            symbols=[{
                "id": "src/client.lua::M::login",
                "name": "login",
                "path": "src/client.lua",
                "freshness": "fresh",
                "confidence": 1.0,
                "recall_channel": "symbol_alias",
                "matched_term": "login",
            }],
            relations=[],
        )

        self.assertEqual(result["required_symbols"], [])

    def test_qualified_exact_matched_term_promotes_its_symbol_only(self) -> None:
        result = self.planner.plan(
            task="AccountApi.login 到 AccountComponent.do_login 的调用链",
            query_profile="call_path",
            core_file_paths=["src/api.lua", "src/component.lua"],
            symbols=[
                {
                    "id": "src/api.lua::AccountApi::login",
                    "name": "login", "path": "src/api.lua", "freshness": "fresh",
                    "recall_channel": "symbol_exact", "matched_term": "AccountApi.login",
                },
                {
                    "id": "src/component.lua::AccountComponent::do_login",
                    "name": "do_login", "path": "src/component.lua", "freshness": "fresh",
                    "recall_channel": "symbol_exact", "matched_term": "AccountComponent.do_login",
                },
                {
                    "id": "src/component.lua::AccountComponent::_finalize_login",
                    "name": "_finalize_login", "path": "src/component.lua", "freshness": "fresh",
                    "recall_channel": "symbol_exact", "matched_term": "do_login",
                },
            ],
            relations=[],
        )

        self.assertEqual(
            [item["payload"]["symbol_id"] for item in result["required_symbols"]],
            [
                "src/api.lua::AccountApi::login",
                "src/component.lua::AccountComponent::do_login",
            ],
        )

    def test_relation_with_unlocated_endpoint_is_not_required(self) -> None:
        result = self.planner.plan(
            task="src/app.py::login 调用链",
            query_profile="call_path",
            core_file_paths=["src/app.py"],
            symbols=[
                {
                    "id": "src/app.py::login",
                    "name": "login",
                    "path": "src/app.py",
                    "freshness": "fresh",
                }
            ],
            relations=[
                {
                    "source": "src/app.py::login",
                    "target": "unknown::handler",
                    "kind": "calls",
                    "resolved": True,
                    "freshness": "fresh",
                }
            ],
        )

        self.assertEqual(result["required_relation_paths"], [])

    def test_planner_contract_exposes_no_budget_or_oracle_inputs(self) -> None:
        import inspect

        parameters = inspect.signature(self.planner.plan).parameters
        self.assertNotIn("max_tokens", parameters)
        self.assertNotIn("token_budget", parameters)
        self.assertNotIn("expected_symbols", parameters)
        self.assertNotIn("required_evidence", parameters)


if __name__ == "__main__":
    unittest.main()
