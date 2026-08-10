from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from pathlib import Path

from project_knowledge.config import ProjectConfig
from project_knowledge.engine import BuiltinCodeIndexEngine
from project_knowledge.real_project import inspect_readonly_scope, run_readonly_mirror
from evaluation.real_project_harness import main as real_project_main


class LuaSkynetEvidenceWP02Tests(unittest.TestCase):
    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "dev").mkdir()
        (root / "service").mkdir()
        (root / "bootstrap").mkdir()
        (root / "dev" / "main.lua").write_text(
            'local skynet = require "skynet"\nskynet.start(function() end)\n',
            encoding="utf-8",
        )
        (root / "service" / "gateway.lua").write_text(
            'local skynetx = require("skynetx")\nskynetx.start(function() end)\n',
            encoding="utf-8",
        )
        (root / "bootstrap" / "protocol.lua").write_text(
            'local skynet = require "skynet"\nskynet.dispatch("lua", function() end)\n',
            encoding="utf-8",
        )
        (root / "bootstrap.lua").write_text(
            'return require "dev.main"\n',
            encoding="utf-8",
        )
        (root / "service" / "player.lua").write_text(
            'function CMD.run() return 1 end\n',
            encoding="utf-8",
        )
        (root / ".svn").mkdir()
        (root / ".svn" / "entries").write_text("private", encoding="utf-8")
        return temporary, root, ProjectConfig(project_name="fixture", exclude=[".svn/**"])

    def test_entrypoints_expose_framework_and_inferred_startup_evidence(self) -> None:
        temporary, root, config = self._fixture()
        self.addCleanup(temporary.cleanup)
        entries = BuiltinCodeIndexEngine().entrypoints(root, config)
        kinds = {entry["kind"] for entry in entries}
        paths = {entry["path"] for entry in entries}
        self.assertIn("skynet_start", kinds)
        self.assertIn("protocol_dispatch", kinds)
        self.assertIn("dev/main.lua", paths)
        self.assertTrue(all(entry["line"] >= 1 for entry in entries))
        self.assertTrue(all(entry["confidence"] > 0 for entry in entries))

    def test_scope_dry_run_reports_exclusions_risks_and_file_hash_revision(self) -> None:
        temporary, root, _config = self._fixture()
        self.addCleanup(temporary.cleanup)
        report = inspect_readonly_scope(root)
        self.assertEqual(report["selected_files"], 5)
        self.assertEqual(report["revision"]["mode"], "file_hash_only")
        self.assertTrue(report["revision"]["value"].startswith("sha256:"))
        self.assertTrue(any(item["pattern"] == ".svn/**" for item in report["excluded_files"]))
        self.assertTrue(any(item["code"] == "excluded_metadata" for item in report["risks"]))

    def test_real_project_harness_dry_run_is_read_only_and_machine_readable(self) -> None:
        temporary, root, _config = self._fixture()
        self.addCleanup(temporary.cleanup)
        before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["real_project_harness", str(root), "--dry-run"]):
            with redirect_stdout(output):
                exit_code = real_project_main()
        after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(before, after)
        report = json.loads(output.getvalue())
        self.assertIn("risks", report)
        self.assertEqual(report["revision"]["mode"], "file_hash_only")

    def test_readonly_mirror_carries_entrypoint_and_revision_evidence(self) -> None:
        temporary, root, _config = self._fixture()
        self.addCleanup(temporary.cleanup)
        report = run_readonly_mirror(root)
        self.assertTrue(report["source"]["unchanged"])
        self.assertEqual(report["source"]["revision_mode"], "file_hash_only")
        self.assertGreaterEqual(report["entrypoints"]["count"], 3)
        self.assertIn("skynet_start", report["entrypoints"]["kinds"])
        self.assertIn("protocol_dispatch", report["entrypoints"]["kinds"])


if __name__ == "__main__":
    unittest.main()
