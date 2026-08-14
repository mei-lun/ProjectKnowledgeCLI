from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_knowledge.codegraph import CodeGraphClient, CodeGraphError, CodeGraphEngine
from project_knowledge.config import ProjectConfig
from project_knowledge.engine import create_engine


class CodeGraphTests(unittest.TestCase):
    def _client(self, outputs: list[tuple[int, str, str]]) -> tuple[CodeGraphClient, Mock]:
        runner = Mock(side_effect=[subprocess.CompletedProcess([], code, out, err) for code, out, err in outputs])
        config = ProjectConfig(codegraph_command="/usr/bin/codegraph", codegraph_timeout_seconds=7)
        client = CodeGraphClient(Path("/mnt/d/Github-Poj/gardenserver"), config, runner=runner)
        return client, runner

    def test_query_status_files_and_impact_parse_public_json(self) -> None:
        client, runner = self._client([
            (0, json.dumps({"initialized": True, "version": "1.5.0"}), ""),
            (0, json.dumps([{"path": "src/app.lua", "language": "lua", "size": 10, "contentHash": "sha256:x"}]), ""),
            (0, json.dumps([{"node": {"id": "src/app.lua::login", "name": "login", "kind": "function", "filePath": "src/app.lua", "startLine": 4, "endLine": 6}}]), ""),
            (0, json.dumps({"symbol": "login", "affected": [{"name": "route", "filePath": "src/router.lua", "startLine": 2}]}), ""),
        ])
        self.assertTrue(client.status()["initialized"])
        self.assertEqual(client.files()[0]["path"], "src/app.lua")
        self.assertEqual(client.query("login")[0]["node"]["name"], "login")
        self.assertEqual(client.impact("login")["affected"][0]["filePath"], "src/router.lua")
        self.assertEqual(runner.call_count, 4)
        status_call = runner.call_args_list[0].args[0]
        self.assertIn("status", " ".join(status_call))
        self.assertIn("--json", " ".join(status_call))
        status_kwargs = runner.call_args_list[0].kwargs
        self.assertEqual(status_kwargs["env"]["CODEGRAPH_DIR"], ".codegraph")
        self.assertEqual(status_kwargs["encoding"], "utf-8")
        self.assertEqual(status_kwargs["errors"], "replace")

    def test_non_utf8_codegraph_output_does_not_crash_windows_cli(self) -> None:
        client, _ = self._client([(0, "{\"initialized\": true}", b"\\x80")])
        self.assertTrue(client.status()["initialized"])

    def test_nonzero_and_invalid_json_are_visible(self) -> None:
        client, _ = self._client([(1, "", "not initialized")])
        with self.assertRaisesRegex(CodeGraphError, "退出码 1"):
            client.status()
        client, _ = self._client([(0, "not-json", "")])
        with self.assertRaisesRegex(CodeGraphError, "无效 JSON"):
            client.files()

    def test_timeout_is_reported_as_codegraph_error(self) -> None:
        runner = Mock(side_effect=subprocess.TimeoutExpired(["codegraph", "status"], 7))
        config = ProjectConfig(codegraph_command="/usr/bin/codegraph", codegraph_timeout_seconds=7)
        client = CodeGraphClient(Path("/mnt/d/Github-Poj/gardenserver"), config, runner=runner)

        with self.assertRaises(CodeGraphError):
            client.status()

    def test_codegraph_engine_is_selectable(self) -> None:
        engine = create_engine(ProjectConfig(engine="codegraph", codegraph_command="/usr/bin/codegraph"))
        self.assertIsInstance(engine, CodeGraphEngine)

    def test_snapshot_preserves_dotfile_names_and_rejects_paths_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".project-kb.yml").write_text("version: 1\n", encoding="utf-8")
            runner = Mock(return_value=subprocess.CompletedProcess(
                [], 0, json.dumps([{"path": ".project-kb.yml", "language": "yaml"}]), ""
            ))
            client = CodeGraphClient(root, ProjectConfig(codegraph_command="/usr/bin/codegraph"), runner=runner)
            snapshot = client.snapshot()
            self.assertEqual(snapshot["files"][0]["path"], ".project-kb.yml")
            self.assertTrue(snapshot["files"][0]["content_hash"].startswith("sha256:"))

            runner.return_value = subprocess.CompletedProcess(
                [], 0, json.dumps([{"path": "../outside.lua", "language": "lua"}]), ""
            )
            with self.assertRaisesRegex(CodeGraphError, "越界"):
                client.snapshot()

    def test_snapshot_hashes_only_files_inside_configured_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "app.lua").write_text("return true\n", encoding="utf-8")
            runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps([
                {"path": "src/app.lua", "language": "lua"},
                {"path": "tools/missing.py", "language": "python"},
            ]), ""))
            config = ProjectConfig(
                codegraph_command="/usr/bin/codegraph", include=["src/**"], exclude=["tools/**"]
            )
            snapshot = CodeGraphClient(root, config, runner=runner).snapshot()
            self.assertEqual([item["path"] for item in snapshot["files"]], ["src/app.lua"])

    def test_engine_normalizes_codegraph_impact_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.project = root.resolve()
            client.impact.return_value = {
                "symbol": "login",
                "affected": [
                    {"id": "route", "name": "route", "filePath": "src/router.lua", "startLine": 2}
                ],
            }
            client.affected_tests.return_value = {"affectedTests": ["tests/router_spec.lua"]}
            engine = CodeGraphEngine(ProjectConfig(engine="codegraph"))
            engine.client = client

            result = engine.impact(
                root, engine.config, symbols=["src/app.lua::login"], max_hops=2
            )

            self.assertEqual(result["affected_files"], ["src/router.lua"])
            self.assertEqual(result["affected_symbols"], ["route"])
            self.assertEqual(result["affected_modules"], ["router.lua"])
            self.assertEqual(result["affected_tests"], ["tests/router_spec.lua"])
            self.assertEqual(result["relations"][0]["source"], "src/app.lua::login")
            self.assertEqual(result["relations"][0]["target"], "route")

    def test_status_reports_uninitialized_reason_without_builtin_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.project = root.resolve()
            client.command_display = "codegraph"
            client.status.return_value = {"initialized": False, "version": "1.5.0"}
            engine = CodeGraphEngine(ProjectConfig(engine="codegraph"))
            engine.client = client

            status = engine.diagnose(root)

            self.assertFalse(status["available"])
            self.assertEqual(status["reason_code"], "project_not_initialized")
            self.assertEqual(status["adapter_version"], "1.5.0")
            self.assertNotIn("builtin", json.dumps(status))

    def test_diagnose_reports_missing_cli_and_command_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = CodeGraphEngine(ProjectConfig(engine="codegraph", codegraph_command="Z:/missing/codegraph"))
            with patch(
                "project_knowledge.codegraph.CodeGraphCommandResolver.resolve",
                side_effect=CodeGraphError("missing CLI"),
            ):
                missing_status = missing.diagnose(root)
            self.assertFalse(missing_status["available"])
            self.assertEqual(missing_status["reason_code"], "cli_missing")

            for message in ("command timed out", "invalid JSON", "exit code 1"):
                client = Mock()
                client.project = root.resolve()
                client.command_display = "codegraph"
                client.status.side_effect = CodeGraphError(message)
                engine = CodeGraphEngine(ProjectConfig(engine="codegraph"))
                engine.client = client
                status = engine.diagnose(root)
                self.assertFalse(status["available"])
                self.assertEqual(status["reason_code"], "command_failed")
                self.assertIn(message, status["details"])

    def test_engine_rejects_external_paths_and_missing_node_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = CodeGraphEngine(ProjectConfig(engine="codegraph"))
            client = Mock()
            client.project = root.resolve()
            engine.client = client

            client.query.return_value = [{"node": {"id": "outside", "filePath": "../outside.lua"}}]
            with self.assertRaisesRegex(CodeGraphError, "outside project"):
                engine.search_symbols(root, engine.config, "outside")

            client.query.return_value = [{"node": {"name": "", "filePath": "src/app.lua"}}]
            with self.assertRaisesRegex(CodeGraphError, "missing identity"):
                engine.search_symbols(root, engine.config, "missing")

    def test_engine_normalizes_callers_and_callees_as_relations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.project = root.resolve()
            client.callers.return_value = {
                "callers": [{"id": "src/router.lua::route", "filePath": "src/router.lua", "startLine": 3}]
            }
            client.callees.return_value = {
                "callees": [{"id": "src/db.lua::load", "filePath": "src/db.lua", "startLine": 8}]
            }
            engine = CodeGraphEngine(ProjectConfig(engine="codegraph"))
            engine.client = client

            relations = engine.trace(root, "src/app.lua::login", engine.config)

            self.assertEqual(
                [(item.source, item.target) for item in relations],
                [
                    ("src/router.lua::route", "src/app.lua::login"),
                    ("src/app.lua::login", "src/db.lua::load"),
                ],
            )
            self.assertEqual([item.path for item in relations], ["src/router.lua", "src/db.lua"])


if __name__ == "__main__":
    unittest.main()
