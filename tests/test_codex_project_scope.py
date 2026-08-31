from __future__ import annotations

import os
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from project_knowledge.codex import codex_mcp_body, migrate_legacy_codex


class CodexProjectScopeTests(unittest.TestCase):
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
