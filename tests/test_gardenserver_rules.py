from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_knowledge.gardenserver import GuidanceEvidenceCollector, GardenserverRuleAdapter


class GardenserverRuleTests(unittest.TestCase):
    def _root(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "src/app/game/magent/msg").mkdir(parents=True)
        (root / "src/app/game/magent/avatar").mkdir(parents=True)
        (root / "src/app/login").mkdir(parents=True)
        (root / "src/appsrv/guild").mkdir(parents=True)
        (root / "src/app/game/magent/msg/garden.lua").write_text('zn.func_mod("GardenMsg", "MsgApi")\nlocal x = require "app.game.magent.system.garden_sys"\n', encoding="utf-8")
        (root / "src/app/game/magent/avatar/avatar_def.lua").write_text('local avatar = { components = { garden = true }, systems = { garden = true } }\n', encoding="utf-8")
        (root / "src/app/login/main.lua").write_text('zn.startup_app(function() zapi.cluster.login() end)\n', encoding="utf-8")
        (root / "src/appsrv/guild/guild.lua").write_text('local result = zn.req(".guild")\n', encoding="utf-8")
        return temporary, root

    def test_extracts_gardenserver_rules_and_marks_forbidden_api(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        facts = GardenserverRuleAdapter(root).collect("player-feature-development", sample_terms=("花园", "公会"))["facts"]
        kinds = {item["kind"] for item in facts}
        self.assertIn("message_module", kinds)
        self.assertIn("avatar_registration", kinds)
        self.assertIn("rpc_call", kinds)
        self.assertTrue(all(item["path"] and item["line"] >= 1 for item in facts))

    def test_collect_all_returns_three_categories(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        result = GuidanceEvidenceCollector(root).collect_all()
        self.assertEqual(set(result), {"activity-development", "player-feature-development", "login-module-development"})
        self.assertIn("登录", result["login-module-development"]["samples"])
        self.assertIn("花园", result["player-feature-development"]["samples"])


if __name__ == "__main__":
    unittest.main()
