from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_knowledge.config import ProjectConfig
from project_knowledge.util import approx_tokens, marker_update, trim_to_tokens


class ConfigTests(unittest.TestCase):
    def test_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = ProjectConfig(project_name="sample", include=["src/**", "config/**"], debounce_ms=250)
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
            self.assertEqual(actual.drafts_root, "docs/knowledge/drafts")

    def test_default_excludes_keep_evaluation_outputs_out_of_the_test_index(self) -> None:
        config = ProjectConfig()
        self.assertIn("evaluation/reports/**", config.exclude)
        self.assertIn("evaluation/baselines/**", config.exclude)
        self.assertIn("docs/knowledge/drafts/**", config.exclude)

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
        self.assertIn("retrieval.embeddings", fields)
        self.assertIn("privacy.local_only", fields)
        self.assertIn("privacy.telemetry", fields)


if __name__ == "__main__":
    unittest.main()
