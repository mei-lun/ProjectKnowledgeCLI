from __future__ import annotations

import os
import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from project_knowledge.codex import codex_mcp_body, migrate_legacy_codex, verify_project_mcp


class CodexProjectScopeTests(unittest.TestCase):
    def test_verify_project_mcp_accepts_batched_jsonrpc_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "project-kb.cmd"
            launcher.write_text("", encoding="utf-8")
            stdout = json.dumps([
                {"jsonrpc": "2.0", "id": 1, "result": {}},
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": {"tools": [
                    {"name": "knowledge_status"},
                    {"name": "knowledge_context"},
                    {"name": "knowledge_impact"},
                ]}}},
            ])
            completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
            with patch("project_knowledge.codex.subprocess.run", return_value=completed):
                result = verify_project_mcp(root, launcher=launcher)
            self.assertTrue(result["verified"])
            self.assertEqual(result["tools"], ["knowledge_context", "knowledge_impact", "knowledge_status"])

    def test_verify_project_mcp_ignores_non_object_batch_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "project-kb.cmd"
            launcher.write_text("", encoding="utf-8")
            stdout = json.dumps([None, ["not-a-response"], {"jsonrpc": "2.0", "id": 1, "result": {}}])
            completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
            with patch("project_knowledge.codex.subprocess.run", return_value=completed):
                result = verify_project_mcp(root, launcher=launcher)
            self.assertFalse(result["verified"])
            self.assertEqual(result["tools"], [])

    def test_project_block_is_absolute_and_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文 project"
            root.mkdir()
            body = codex_mcp_body(root, launcher=Path("C:/Users/test/project-kb.cmd"), codegraph_command=Path("C:/Tools/codegraph.cmd"))
            parsed = tomllib.loads(body)
            server = parsed["mcp_servers"]["project_knowledge"]
            self.assertEqual(server["cwd"], str(root.resolve()))
            self.assertEqual(server["args"], ["mcp", "--project", "."])
            self.assertEqual(Path(server["env"]["CODEGRAPH_COMMAND"]), Path("C:/Tools/codegraph.cmd"))

    def test_migrate_removes_only_matching_project_and_keeps_other_servers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "garden"
            root.mkdir()
            config = Path(directory) / "config.toml"
            other = Path(directory) / "billing"
            other.mkdir()
            config.write_text(
                "[mcp_servers.project_knowledge_garden]\ncommand = \"project-kb\"\nargs = [\"mcp\", \"--project\", %s]\n\n"
                "[mcp_servers.project_knowledge_billing]\ncommand = \"project-kb\"\ncwd = %s\n\n"
                "[mcp_servers.other]\ncommand = \"custom\"\n" % (json.dumps(str(root)), json.dumps(str(other))),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                result = migrate_legacy_codex(root, user_config=config)
            self.assertEqual(result["removed"], ["project_knowledge_garden"])
            self.assertTrue(Path(result["backup"]).exists())
            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertIn("project_knowledge_billing", parsed["mcp_servers"])
            self.assertIn("other", parsed["mcp_servers"])

    def test_migrate_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text("[mcp_servers.project_knowledge]\ncommand = \"project-kb\"\ncwd = %s\n" % json.dumps(str(root)), encoding="utf-8")
            first = migrate_legacy_codex(root, user_config=config)
            second = migrate_legacy_codex(root, user_config=config)
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])


if __name__ == "__main__":
    unittest.main()
