from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from project_knowledge.mcp import MCPServer, TOOLS


class GuidanceMCPTests(unittest.TestCase):
    def test_lists_seven_workflow_tools(self):
        self.assertEqual(len(TOOLS), 12)
        names = {item["name"] for item in TOOLS}
        self.assertTrue({
            "knowledge_initialization_start", "knowledge_initialization_next",
            "knowledge_initialization_submit", "knowledge_draft_save",
            "knowledge_draft_confirm", "knowledge_changes", "knowledge_update_submit",
        }.issubset(names))
        for item in TOOLS[5:]:
            self.assertFalse(item["annotations"]["destructiveHint"])
        for name in {
            "knowledge_initialization_start", "knowledge_initialization_submit",
            "knowledge_draft_save", "knowledge_draft_confirm", "knowledge_update_submit",
        }:
            tool = next(item for item in TOOLS if item["name"] == name)
            self.assertFalse(tool["annotations"]["readOnlyHint"])

    def test_initialization_and_draft_routes_are_explicit(self):
        server = object.__new__(MCPServer)
        server.project = Path("/tmp/project")
        server.api = None
        with patch("project_knowledge.initialization.InitializationWorkflow") as workflow:
            workflow.return_value.start.return_value = {"status": "scanning"}
            self.assertEqual(server._call("knowledge_initialization_start", {})["status"], "scanning")
            workflow.return_value.start.assert_called_once_with()
        with patch("project_knowledge.guidance_workflow.GuidanceWorkflow") as workflow:
            workflow.return_value.confirm_draft.return_value = {"status": "confirmed"}
            result = server._call("knowledge_draft_confirm", {
                "draftId": "d1", "contentHash": "sha256:" + "0" * 64, "reviewer": "mei",
            })
            self.assertEqual(result["status"], "confirmed")
            workflow.return_value.confirm_draft.assert_called_once_with("d1", "sha256:" + "0" * 64, "mei")

    def test_save_and_reject_require_conditional_fields(self):
        server = object.__new__(MCPServer)
        server.project = Path("/tmp/project")
        server.api = None
        with patch("project_knowledge.guidance_workflow.GuidanceWorkflow"):
            with self.assertRaisesRegex(ValueError, "缺少字段"):
                server._call("knowledge_draft_save", {"action": "save"})
            with self.assertRaisesRegex(ValueError, "缺少字段"):
                server._call("knowledge_draft_save", {"action": "reject", "draftId": "d"})

    def test_incremental_routes_are_explicit(self):
        server = object.__new__(MCPServer)
        server.project = Path("/tmp/project")
        server.api = None
        with patch("project_knowledge.incremental.IncrementalWorkflow") as workflow:
            workflow.return_value.changes.return_value = {"status": "pending"}
            self.assertEqual(server._call("knowledge_changes", {})["status"], "pending")
            workflow.return_value.submit_update.return_value = {"status": "completed"}
            result = server._call("knowledge_update_submit", {
                "changeId": "c1", "level": "fact", "categoryId": "login",
                "content": {"guidance_unchanged": True},
                "evidence": [{"path": "a.lua", "hash": "h"}],
            })
            self.assertEqual(result["status"], "completed")
            workflow.return_value.submit_update.assert_called_once_with(
                "c1", "fact", category_id="login",
                content={"guidance_unchanged": True},
                evidence=[{"path": "a.lua", "hash": "h"}],
            )

    def test_tool_failure_is_error_and_server_keeps_responding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = object.__new__(MCPServer)
            server.project = root
            server.input = StringIO()
            server.output = StringIO()
            server.api = None
            failed = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "knowledge_draft_save", "arguments": {"action": "save"}},
            })
            self.assertTrue(failed["result"]["isError"])
            pong = server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
            self.assertEqual(pong["result"], {})


if __name__ == "__main__":
    unittest.main()
