from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_knowledge.config import ProjectConfig
from project_knowledge.errors import UnsupportedEngineError
from project_knowledge.util import approx_tokens, marker_update, trim_to_tokens


class ConfigTests(unittest.TestCase):
    def test_default_engine_is_codegraph(self) -> None:
        self.assertEqual(ProjectConfig().engine, "codegraph")
        self.assertFalse(ProjectConfig().mcp_audit_enabled)

    def test_legacy_builtin_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".project-kb.yml").write_text(
                "version: 1\nindex:\n  engine: builtin\n", encoding="utf-8"
            )
            with self.assertRaises(UnsupportedEngineError) as raised:
                ProjectConfig.load(root)
            self.assertEqual(
                raised.exception.to_dict(),
                {
                    "error": "unsupported_engine",
                    "configured_engine": "builtin",
                    "supported_engines": ["codegraph"],
                    "migration": "set index.engine to codegraph and initialize CodeGraph for this project",
                },
            )

    def test_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = ProjectConfig(
                project_name="sample", include=["src/**", "config/**"],
                debounce_ms=250, mcp_audit_enabled=True,
            )
            expected.write(root)
            actual = ProjectConfig.load(root)
            self.assertEqual(actual.project_name, "sample")
            self.assertEqual(actual.include, ["src/**", "config/**"])
            self.assertEqual(actual.debounce_ms, 250)
            self.assertTrue(actual.local_only)
            self.assertEqual(actual.provider_id, "disabled")
            self.assertFalse(actual.provider_enabled)
            self.assertFalse(actual.provider_allow_network)
            self.assertEqual(actual.provider_max_tokens, 12000)
            self.assertEqual(actual.drafts_root, ".project-kb/drafts")
            self.assertEqual(actual.codegraph_dir, ".codegraph")
            self.assertEqual(actual.ranking_policy, "policy-v2")
            self.assertTrue(actual.mcp_audit_enabled)

    def test_ranking_policy_round_trip_supports_policy_v1_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ProjectConfig(ranking_policy="policy-v1").write(root)

            actual = ProjectConfig.load(root)

            self.assertEqual(actual.ranking_policy, "policy-v1")

    def test_mcp_audit_toggle_supports_json_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / ".project-kb.yml"
            path.write_text(
                '{"version":1,"custom":{"retained":true}}\n', encoding="utf-8",
            )

            result = ProjectConfig.set_mcp_audit_enabled(root, True)

            self.assertTrue(result["enabled"])
            self.assertTrue(ProjectConfig.load(root).mcp_audit_enabled)
            self.assertIn('"retained": true', path.read_text(encoding="utf-8"))

    def test_mcp_audit_toggle_does_not_rewrite_nested_custom_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / ".project-kb.yml"
            path.write_text(
                "version: 1\nobservability: # owned settings\n"
                "  custom:\n    mcp_audit_enabled: false\n",
                encoding="utf-8",
            )

            ProjectConfig.set_mcp_audit_enabled(root, True)

            self.assertTrue(ProjectConfig.load(root).mcp_audit_enabled)
            rendered = path.read_text(encoding="utf-8")
            self.assertIn("    mcp_audit_enabled: false", rendered)
            self.assertIn("  mcp_audit_enabled: true", rendered)

    def test_mcp_audit_config_rejects_quoted_boolean_instead_of_enabling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".project-kb.yml").write_text(
                'version: 1\nobservability:\n  mcp_audit_enabled: "false"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "必须是布尔值"):
                ProjectConfig.load(root)

    def test_default_excludes_keep_evaluation_outputs_out_of_the_test_index(self) -> None:
        config = ProjectConfig()
        self.assertIn("evaluation/reports/**", config.exclude)
        self.assertIn("evaluation/baselines/**", config.exclude)
        self.assertIn(".project-kb/drafts/**", config.exclude)

    def test_marker_update_preserves_unowned_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "AGENTS.md"
            path.write_text("User content\n", encoding="utf-8")
            self.assertTrue(marker_update(path, "instructions", "Owned content"))
            self.assertIn("User content", path.read_text(encoding="utf-8"))
            self.assertTrue(marker_update(path, "instructions", None))
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "User content")

    def test_token_trimming_respects_budget(self) -> None:
        text = "hello world " * 1000
        trimmed = trim_to_tokens(text, 80)
        self.assertLessEqual(approx_tokens(trimmed), 90)
        self.assertIn("truncated", trimmed)

    def test_capability_warnings_name_every_unwired_setting(self) -> None:
        config = ProjectConfig(embeddings="local", local_only=False, telemetry=True)
        fields = {item["field"] for item in config.capability_warnings()}
        self.assertNotIn("updates.curated_mode", fields)
        self.assertNotIn("updates.proposal_trigger", fields)
        self.assertNotIn("retrieval.embeddings", fields)
        self.assertIn("privacy.local_only", fields)
        self.assertIn("privacy.telemetry", fields)


if __name__ == "__main__":
    unittest.main()
