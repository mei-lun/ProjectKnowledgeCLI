from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_knowledge.config import ProjectConfig
from project_knowledge.evidence import EvidencePackBuilder, EvidencePolicyError
from project_knowledge.service import ProjectService
from project_knowledge.schemas import CONFIG_SCHEMA, validate_instance


class ConfigMigrationAndClientTests(unittest.TestCase):
    def _project(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        return temporary, root

    def test_config_schema_is_published_and_accepts_user_extension(self):
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        schema_path = root / ".project-kb" / "schemas" / "config-v1.json"
        self.assertTrue(schema_path.exists())
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], CONFIG_SCHEMA["$id"])
        payload = {"version": 1, "project": {"name": "sample"}, "user_extension": {"team": "core"}}
        validate_instance(payload, CONFIG_SCHEMA)

    def test_migrate_dry_run_and_apply_preserve_user_json_fields(self):
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        config_path = root / ".project-kb.yml"
        original = {
            "version": 0,
            "project": {"name": "legacy"},
            "user_extension": {"owner": "team-a"},
        }
        config_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        service = ProjectService(root)
        preview = service.migrate(dry_run=True)
        self.assertTrue(preview["changed"])
        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["version"], 0)
        result = service.migrate()
        self.assertTrue(result["changed"])
        migrated = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 1)
        self.assertEqual(migrated["user_extension"], {"owner": "team-a"})

    def test_plugin_manifest_version_matches_core_version(self):
        import project_knowledge
        manifest = json.loads(
            (Path(__file__).parents[1] / "plugins" / "project-knowledge" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], project_knowledge.__version__)

    def test_install_and_uninstall_supported_client_markers_preserve_user_content(self):
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        service = ProjectService(root)
        service.initialize()
        result = service.install()
        self.assertEqual(set(result["clients"]), {"claude", "cursor", "gemini"})
        service.install()
        claude = root / ".claude" / "CLAUDE.md"
        cursor = root / ".cursor" / "rules" / "project-knowledge.mdc"
        gemini = root / "GEMINI.md"
        for path in [claude, cursor, gemini]:
            self.assertTrue(path.exists())
            self.assertIn("project-kb:", path.read_text(encoding="utf-8"))
            self.assertEqual(path.read_text(encoding="utf-8").count("project-kb:"), 2)
        claude.write_text("User rule\n\n" + claude.read_text(encoding="utf-8"), encoding="utf-8")
        removed = service.uninstall()
        self.assertEqual(set(removed["clients_removed"]), {"claude", "cursor", "gemini"})
        self.assertIn("User rule", claude.read_text(encoding="utf-8"))
        self.assertNotIn("project-kb:claude:start", claude.read_text(encoding="utf-8"))
        self.assertNotIn("project-kb:cursor:start", cursor.read_text(encoding="utf-8"))
        self.assertNotIn("project-kb:gemini:start", gemini.read_text(encoding="utf-8"))
        self.assertNotIn("project-kb:claude:end", claude.read_text(encoding="utf-8"))
        self.assertNotIn("project-kb:cursor:end", cursor.read_text(encoding="utf-8"))
        self.assertNotIn("project-kb:gemini:end", gemini.read_text(encoding="utf-8"))

    def test_native_and_wsl_relative_paths_work_but_windows_absolute_path_is_rejected(self):
        temporary, root = self._project()
        self.addCleanup(temporary.cleanup)
        builder = EvidencePackBuilder(root)
        pack = builder.build("验证跨平台路径", ["src/app.py"])
        self.assertEqual(pack.items[0].path, "src/app.py")
        with self.assertRaises(EvidencePolicyError):
            builder.build("阻止越界", [r"C:\Users\mei\secret.py"])


if __name__ == "__main__":
    unittest.main()
