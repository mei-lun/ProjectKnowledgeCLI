from __future__ import annotations

import tempfile
import tomllib
import unittest
from datetime import date
from pathlib import Path

from project_knowledge import __version__
from project_knowledge.versioning import bump_patch_version, next_patch_version, read_project_version


class VersioningTests(unittest.TestCase):
    def test_package_version_is_the_single_build_source(self) -> None:
        root = Path(__file__).resolve().parents[1]
        configuration = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(read_project_version(root), __version__)
        self.assertEqual(configuration["project"]["dynamic"], ["version"])
        self.assertEqual(
            configuration["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "project_knowledge.__version__",
        )

    def test_patch_version_increment_updates_version_and_changelog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "src" / "project_knowledge"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
            (root / "CHANGELOG.md").write_text(
                "# 变更日志\n\n## [0.1.0] - 2026-08-06\n\n- 初始版本。\n",
                encoding="utf-8",
            )
            plugin = root / "plugins" / "project-knowledge" / ".codex-plugin" / "plugin.json"
            plugin.parent.mkdir(parents=True)
            plugin.write_text('{"name": "project-knowledge", "version": "0.1.0"}\n', encoding="utf-8")

            old_version, new_version = bump_patch_version(
                root,
                "增加版本控制。",
                changed_on=date(2026, 8, 7),
            )

            self.assertEqual((old_version, new_version), ("0.1.0", "0.1.1"))
            self.assertEqual(read_project_version(root), "0.1.1")
            changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertLess(changelog.index("## [0.1.1]"), changelog.index("## [0.1.0]"))
            self.assertIn("增加版本控制。", changelog)
            self.assertEqual(__import__("json").loads(plugin.read_text(encoding="utf-8"))["version"], "0.1.1")

    def test_dry_run_does_not_modify_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "src" / "project_knowledge"
            package.mkdir(parents=True)
            version_path = package / "__init__.py"
            version_path.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
            plugin = root / "plugins" / "project-knowledge" / ".codex-plugin" / "plugin.json"
            plugin.parent.mkdir(parents=True)
            plugin.write_text('{"name": "project-knowledge", "version": "1.2.3"}\n', encoding="utf-8")

            self.assertEqual(next_patch_version("1.2.3"), "1.2.4")
            self.assertEqual(
                bump_patch_version(root, "预览。", dry_run=True),
                ("1.2.3", "1.2.4"),
            )
            self.assertEqual(read_project_version(root), "1.2.3")
            self.assertFalse((root / "CHANGELOG.md").exists())
            self.assertEqual(__import__("json").loads(plugin.read_text(encoding="utf-8"))["version"], "1.2.3")


if __name__ == "__main__":
    unittest.main()
