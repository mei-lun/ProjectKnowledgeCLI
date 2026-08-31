from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from project_knowledge.engine import CodeIndexSnapshot
from project_knowledge.service import ProjectService


class StubEngine:
    def __init__(self, *, fail_initialize: bool = False) -> None:
        self.fail_initialize = fail_initialize

    def initialize(self, root, config):
        if self.fail_initialize:
            raise RuntimeError("codegraph initialization failed")
        return {"initialized": True}

    def snapshot(self, root, config):
        return CodeIndexSnapshot("fixture", ())


class CodexBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.codegraph = (self.root / "tools" / "codegraph.cmd").resolve()
        self.codegraph.parent.mkdir(parents=True)
        self.codegraph.write_text("@echo off\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def service(self, *, fail_initialize: bool = False) -> ProjectService:
        return ProjectService(
            self.root,
            engine_factory=lambda _config: StubEngine(fail_initialize=fail_initialize),
        )

    def initialize(self, service: ProjectService) -> dict[str, object]:
        with (
            patch.dict(os.environ, {"CODEGRAPH_COMMAND": str(self.codegraph)}),
            patch.object(service, "_atomic_rebuild", return_value={"files_indexed": 0}),
        ):
            return service.initialize()

    def test_successful_init_writes_owned_agents_and_codex_mcp_config(self) -> None:
        agents = self.root / "AGENTS.md"
        agents.write_text("User-owned rule\n", encoding="utf-8")

        result = self.initialize(self.service())

        self.assertEqual(result["action"], "init")
        self.assertEqual(result["codex"]["config"], ".codex/config.toml")
        agents_text = agents.read_text(encoding="utf-8")
        self.assertIn("User-owned rule", agents_text)
        self.assertEqual(agents_text.count("<!-- project-kb:instructions:start -->"), 1)

        config_path = self.root / ".codex" / "config.toml"
        config_text = config_path.read_text(encoding="utf-8")
        self.assertEqual(config_text.count("# project-kb:codex-mcp:start"), 1)
        parsed = tomllib.loads(config_text)
        server = parsed["mcp_servers"]["project_knowledge"]
        self.assertEqual(server["command"], "project-kb")
        self.assertEqual(server["args"], ["mcp", "--project", "."])
        self.assertEqual(Path(server["cwd"]), self.root.resolve())
        self.assertTrue(server["enabled"])
        self.assertEqual(Path(server["env"]["CODEGRAPH_COMMAND"]), self.codegraph)
        self.assertIn(
            ".codex/config.toml",
            (self.root / ".gitignore").read_text(encoding="utf-8"),
        )

    def test_init_dry_run_lists_codex_targets_without_writing(self) -> None:
        service = self.service()
        with patch.object(service.engine, "snapshot", return_value=CodeIndexSnapshot("fixture", ())):
            result = service.initialize(dry_run=True)

        self.assertIn("AGENTS.md", result["files_to_create"])
        self.assertIn(".codex/config.toml", result["files_to_create"])
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertFalse((self.root / ".codex").exists())

    def test_failed_codegraph_init_does_not_write_codex_integration(self) -> None:
        service = self.service(fail_initialize=True)

        with self.assertRaisesRegex(RuntimeError, "codegraph initialization failed"):
            self.initialize(service)

        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertFalse((self.root / ".codex" / "config.toml").exists())

    def test_unowned_project_knowledge_table_is_a_conflict(self) -> None:
        config = self.root / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        original = '[mcp_servers.project_knowledge]\ncommand = "user-owned"\n'
        config.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "project_knowledge"):
            self.initialize(self.service())

        self.assertEqual(config.read_text(encoding="utf-8"), original)
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_rerun_is_idempotent_and_uninstall_preserves_unowned_toml(self) -> None:
        config = self.root / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('model = "gpt-5"\n', encoding="utf-8")
        service = self.service()

        self.initialize(service)
        first = config.read_text(encoding="utf-8")
        self.initialize(service)
        self.assertEqual(config.read_text(encoding="utf-8"), first)

        result = service.uninstall(clients=[])

        self.assertTrue(result["codex_removed"])
        remaining = config.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5"', remaining)
        self.assertNotIn("project-kb:codex-mcp", remaining)
        tomllib.loads(remaining)

    def test_invalid_existing_toml_is_rejected_without_changes(self) -> None:
        config = self.root / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        original = "model = [\n"
        config.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "TOML"):
            self.initialize(self.service())

        self.assertEqual(config.read_text(encoding="utf-8"), original)
        self.assertFalse((self.root / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
