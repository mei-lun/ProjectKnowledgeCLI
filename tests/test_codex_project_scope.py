from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from project_knowledge.codex import codex_mcp_body, migrate_legacy_codex, verify_project_mcp


class CodexProjectScopeTests(unittest.TestCase):
    def test_verify_project_mcp_requires_complete_handshake_and_queries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "project-kb.cmd"
            launcher.write_text("", encoding="utf-8")
            stdout = json.dumps([
                {"jsonrpc": "2.0", "id": 1, "result": {}},
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                    {"name": "knowledge_status"},
                    {"name": "knowledge_context"},
                    {"name": "knowledge_impact"},
                ]}},
            ])
            completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
            with patch("project_knowledge.codex.subprocess.run", return_value=completed):
                result = verify_project_mcp(root, launcher=launcher)
            self.assertFalse(result["verified"])

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
            self.assertFalse(result["verified"])

    def run_server(self, mode: str = "success") -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / ".codex" / "config.toml"
            config.parent.mkdir()
            script = '''import json, os, sys
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "notifications/initialized":
        initialized = True
        continue
    ident = request["id"]
    mode = os.environ["VERIFICATION_MODE"]
    if method == "initialize":
        if mode == "timeout":
            import time
            time.sleep(10)
        result = {"protocolVersion": "2026-07-28", "capabilities": {"tools": {}}, "serverInfo": {"name": "project-knowledge", "version": "test"}}
        if mode == "bad_handshake": result = {}
        if mode == "missing_handshake": sys.exit(0)
    elif method == "tools/list":
        assert initialized
        result = {"tools": [{"name": name} for name in ["knowledge_status", "knowledge_context", "knowledge_impact"]]}
        if mode == "nested_tools": result = {"tools": result}
        if mode == "missing_tools": result["tools"].pop()
    else:
        assert initialized
        name = request["params"]["name"]
        payload = {"project_root": os.getcwd()} if name == "knowledge_status" else {"task": request["params"]["arguments"]["task"], "items": []}
        result = {"isError": mode == name, "content": [{"type": "text", "text": json.dumps(payload)}]}
        if mode == "empty_query": result = {"isError": False}
    response = {"jsonrpc": "2.0", "id": ident + 10 if mode == "wrong_id" else ident, "result": result}
    if mode == "rpc_error": response = {"jsonrpc": "2.0", "id": ident, "error": {"code": -1, "message": "broken"}}
    print(json.dumps(response), flush=True)
    if mode == "nonzero_exit" and ident == 4: sys.exit(3)
'''
            config.write_text(
                '[mcp_servers.project_knowledge]\ncommand = ' + json.dumps(sys.executable)
                + '\nargs = ' + json.dumps(["-u", "-c", script])
                + '\ncwd = ' + json.dumps(str(root))
                + '\n[mcp_servers.project_knowledge.env]\nVERIFICATION_MODE = ' + json.dumps(mode),
                encoding="utf-8",
            )
            return verify_project_mcp(root, timeout=0.2 if mode == "timeout" else 3)

    def test_saved_config_command_args_cwd_env_and_initialized_notification(self) -> None:
        result = self.run_server()
        self.assertTrue(result["verified"], result)
        self.assertTrue(result["queries_verified"])
        self.assertFalse(result["codex_session_verified"])

    def test_protocol_and_query_failures_cannot_pass_verification(self) -> None:
        for mode in ["bad_handshake", "missing_handshake", "wrong_id", "nested_tools", "missing_tools", "rpc_error", "knowledge_status", "knowledge_context", "empty_query", "nonzero_exit", "timeout"]:
            with self.subTest(mode=mode):
                result = self.run_server(mode)
                self.assertFalse(result["verified"], result)
                self.assertIn("stage", result)
                self.assertIn("reason", result)
                self.assertIn("next_action", result)

    def test_project_block_is_absolute_and_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文 project"
            root.mkdir()
            body = codex_mcp_body(root, launcher=Path("C:/Users/test/project-kb.cmd"), codegraph_command=Path("C:/Tools/codegraph.cmd"))
            parsed = tomllib.loads(body)
            server = parsed["mcp_servers"]["project_knowledge"]
            self.assertEqual(server["cwd"], str(root.resolve()))
            self.assertEqual(server["args"], ["mcp", "--project", "."])
            self.assertEqual(server["startup_timeout_sec"], 120)
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
