from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from project_knowledge.mcp import MCPServer, TOOLS


class GuidanceMCPTests(unittest.TestCase):
    def test_lists_seven_workflow_tools(self):
        self.assertEqual(len(TOOLS), 14)
        names = {item["name"] for item in TOOLS}
        self.assertTrue({
            "knowledge_initialization_start", "knowledge_initialization_next",
            "knowledge_initialization_submit", "knowledge_draft_save",
            "knowledge_draft_confirm", "knowledge_changes", "knowledge_update_submit",
            "knowledge_guidance_plan", "knowledge_task_complete",
        }.issubset(names))
        draft_tool = next(item for item in TOOLS if item["name"] == "knowledge_draft_save")
        self.assertEqual(
            set(draft_tool["inputSchema"]["properties"]["kind"]["enum"]),
            {"category_catalog", "methodology", "guidance"},
        )
        submit_tool = next(
            item for item in TOOLS if item["name"] == "knowledge_initialization_submit"
        )
        self.assertIn("analyzedFiles", submit_tool["inputSchema"]["required"])
        for item in TOOLS[5:]:
            self.assertFalse(item["annotations"]["destructiveHint"])
        for name in {
            "knowledge_initialization_start", "knowledge_initialization_submit",
            "knowledge_draft_save", "knowledge_draft_confirm", "knowledge_update_submit",
        }:
            tool = next(item for item in TOOLS if item["name"] == name)
            self.assertFalse(tool["annotations"]["readOnlyHint"])

    def test_server_can_start_before_project_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            server = MCPServer(directory, StringIO(), StringIO())
            self.assertIsNone(server.api)
            with patch("project_knowledge.initialization.InitializationWorkflow") as workflow:
                workflow.return_value.start.return_value = {"status": "scanning"}
                response = server.handle({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "knowledge_initialization_start", "arguments": {}},
                })
                self.assertFalse(response["result"]["isError"])

    def test_runtime_validates_published_input_schema(self):
        server = object.__new__(MCPServer)
        server.project = Path("/tmp/project")
        server.api = None
        for arguments in (
            {"changeId": "c1", "level": "unknown"},
            {"changeId": "c1", "level": "fact", "outputPath": "/tmp/result.md"},
        ):
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "knowledge_update_submit", "arguments": arguments},
            })
            self.assertTrue(response["result"]["isError"])

    def test_read_tools_publish_cross_project_and_impact_limits(self):
        schemas = {item["name"]: item["inputSchema"] for item in TOOLS}
        for name in {"knowledge_context", "knowledge_search", "knowledge_get", "knowledge_impact", "knowledge_status"}:
            self.assertIn("projectPath", schemas[name]["properties"])
            self.assertFalse(schemas[name]["additionalProperties"])
        server = object.__new__(MCPServer)
        server.project = Path("/tmp/project")
        server.api = None
        with patch("project_knowledge.mcp.KnowledgeAPI") as api:
            api.return_value.impact.return_value = {"status": "current"}
            server._call("knowledge_impact", {
                "projectPath": "/tmp/other", "files": ["a.lua"],
                "maxHops": 2, "maxRelations": 40,
            })
            api.return_value.impact.assert_called_once_with(["a.lua"], None, 2, 40)

    def test_initialization_and_draft_routes_are_explicit(self):
        server = object.__new__(MCPServer)
        server.project = Path("/tmp/project")
        server.api = None
        with patch("project_knowledge.initialization.InitializationWorkflow") as workflow:
            workflow.return_value.start.return_value = {"status": "scanning"}
            self.assertEqual(server._call("knowledge_initialization_start", {})["status"], "scanning")
            workflow.return_value.start.assert_called_once_with()
        with patch("project_knowledge.initialization.InitializationWorkflow") as workflow:
            workflow.return_value.submit_batch.return_value = {"status": "category_review"}
            server._call("knowledge_initialization_submit", {
                "runId": "run-1", "batchId": "batch-1", "snapshotId": "snap-1",
                "candidates": [], "analyzedFiles": ["a.py"],
            })
            workflow.return_value.submit_batch.assert_called_once_with(
                "run-1", "batch-1", "snap-1", [],
                analyzed_files=["a.py"], error=None,
            )
        with patch("project_knowledge.guidance_workflow.GuidanceWorkflow") as workflow:
            workflow.return_value.confirm_draft.return_value = {"status": "confirmed"}
            result = server._call("knowledge_draft_confirm", {
                "draftId": "d1", "contentHash": "sha256:" + "0" * 64, "reviewer": "mei",
            })
            self.assertEqual(result["status"], "confirmed")
            workflow.return_value.confirm_draft.assert_called_once_with("d1", "sha256:" + "0" * 64, "mei")

    def test_draft_routes_validate_against_the_live_codegraph_client(self):
        server = object.__new__(MCPServer)
        server.project = Path("/tmp/project")
        server.api = None
        with patch("project_knowledge.mcp.CodeGraphClient") as client, patch(
            "project_knowledge.mcp.ProjectConfig.load"
        ) as load, patch("project_knowledge.guidance_workflow.GuidanceWorkflow") as workflow:
            workflow.return_value.save_draft.return_value = {"status": "awaiting_confirmation"}
            server._call("knowledge_draft_save", {
                "action": "save", "kind": "category_catalog", "runId": "run-1", "content": {"categories": []},
            })
            workflow.assert_called_once_with(Path("/tmp/project").resolve(), client=client.return_value)
            load.assert_called_once_with(Path("/tmp/project").resolve())

    def test_task_completion_routes_to_workflow(self):
        server = object.__new__(MCPServer)
        server.project = Path("/tmp/project")
        server.api = None
        with patch("project_knowledge.task_workflow.TaskCompletionWorkflow") as workflow:
            workflow.return_value.complete.return_value = {"task_id": "t1", "next_action": "generate_guidance_draft"}
            result = server._call("knowledge_task_complete", {
                "taskId": "t1", "summary": "完成登录功能", "userConfirmed": True,
                "changedFiles": ["src/login.py"], "tests": [{"command": "pytest", "passed": True}],
            })
            self.assertEqual(result["task_id"], "t1")
            workflow.return_value.complete.assert_called_once_with(
                "t1", "完成登录功能", changed_files=["src/login.py"],
                changed_symbols=[], tests=[{"command": "pytest", "passed": True}],
                user_confirmed=True, skip=False, skip_reason=None,
            )

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
