from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

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

    def test_nonzero_and_invalid_json_are_visible(self) -> None:
        client, _ = self._client([(1, "", "not initialized")])
        with self.assertRaisesRegex(CodeGraphError, "退出码 1"):
            client.status()
        client, _ = self._client([(0, "not-json", "")])
        with self.assertRaisesRegex(CodeGraphError, "无效 JSON"):
            client.files()

    def test_codegraph_engine_is_selectable(self) -> None:
        engine = create_engine(ProjectConfig(engine="codegraph", codegraph_command="/usr/bin/codegraph"))
        self.assertIsInstance(engine, CodeGraphEngine)


if __name__ == "__main__":
    unittest.main()
