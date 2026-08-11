from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_knowledge.guidance import GuidanceService


class GuidanceTests(unittest.TestCase):
    def _root(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "src/app/login").mkdir(parents=True)
        (root / "src/app/game/magent/msg").mkdir(parents=True)
        (root / "src/app/game/magent/avatar").mkdir(parents=True)
        (root / "src/app/login/main.lua").write_text('zn.startup_app(function() end)\n', encoding="utf-8")
        (root / "src/app/game/magent/msg/garden.lua").write_text('zn.func_mod("GardenMsg", "MsgApi")\n', encoding="utf-8")
        (root / "src/app/game/magent/avatar/avatar_def.lua").write_text('local x = { components = {}, systems = {} }\n', encoding="utf-8")
        return temporary, root

    def test_generates_three_chinese_two_layer_guides_under_project_kb(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        legacy = root / ".project-kb" / "generated" / "login-module-development.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("legacy", encoding="utf-8")
        result = GuidanceService(root).generate()
        self.assertEqual(set(result["categories"]), {"activity-development", "player-feature-development", "login-module-development"})
        generated = root / ".project-kb" / "generated"
        self.assertTrue((generated / "开发指导索引.md").exists())
        login = (generated / "登录模块开发.md").read_text(encoding="utf-8")
        self.assertIn("第一层：可迁移方法论", login)
        self.assertIn("第二层：项目适配", login)
        self.assertIn("src/app/login/main.lua:1", login)
        self.assertTrue((root / ".project-kb" / "methodology" / "login-module-development.json").exists())
        self.assertTrue((root / ".project-kb" / "guides" / "login-module-development.json").exists())
        self.assertFalse(legacy.exists())
        self.assertFalse((root / "docs" / "knowledge").exists())

    def test_manifest_records_guidance_root_and_evidence(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        GuidanceService(root).generate()
        manifest = json.loads((root / ".project-kb" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["guidance"]["root"], ".project-kb/generated")
        evidence = json.loads((root / ".project-kb" / "evidence" / "player-feature-development.json").read_text(encoding="utf-8"))
        self.assertIn("花园", evidence["samples"])


if __name__ == "__main__":
    unittest.main()
