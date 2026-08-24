from __future__ import annotations

import unittest
from pathlib import Path

from scripts.validate_npm_bootstrap import launcher_path, validate_mcp_responses


class NpmBootstrapValidationTests(unittest.TestCase):
    def test_windows_launcher_uses_the_isolated_npm_prefix(self) -> None:
        prefix = Path("C:/temporary/npm-prefix")
        self.assertEqual(
            launcher_path(prefix, platform="win32"),
            prefix.resolve() / "project-kb.cmd",
        )

    def test_mcp_validation_requires_handshake_tools_and_successful_status(self) -> None:
        names = validate_mcp_responses([
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"serverInfo": {"name": "project-knowledge", "version": "1.2.3"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [
                    {"name": "knowledge_status"},
                    {"name": "knowledge_context"},
                    {"name": "knowledge_impact"},
                ]},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {"isError": False, "content": [{"type": "text", "text": "{}"}]},
            },
        ])

        self.assertEqual(
            names,
            ["knowledge_context", "knowledge_impact", "knowledge_status"],
        )

    def test_mcp_validation_rejects_missing_required_tools(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "knowledge_impact"):
            validate_mcp_responses([
                {"id": 1, "result": {"serverInfo": {"name": "project-knowledge"}}},
                {"id": 2, "result": {"tools": [{"name": "knowledge_status"}, {"name": "knowledge_context"}]}},
                {"id": 3, "result": {"isError": False}},
            ])


if __name__ == "__main__":
    unittest.main()
