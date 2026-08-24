from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_npm_package import stage_npm_package


class NpmPackageBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        package = self.root / "src" / "project_knowledge"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        npm = self.root / "npm"
        for directory in ["bin", "lib", "scripts"]:
            target = npm / directory
            target.mkdir(parents=True)
            (target / "placeholder.js").write_text("module.exports = {};\n", encoding="utf-8")
        (npm / "package.template.json").write_text(
            json.dumps({
                "name": "project-kb-cli",
                "bin": {"project-kb": "bin/placeholder.js"},
                "files": ["bin", "lib", "scripts", "vendor"],
            }),
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("# Test package\n", encoding="utf-8")
        (self.root / "LICENSE").write_text("Apache-2.0 test license\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stage_generates_manifest_from_python_version_and_bundles_one_wheel(self) -> None:
        wheel = self.root / "wheelhouse" / "project_knowledge_cli-1.2.3-py3-none-any.whl"
        wheel.parent.mkdir()
        wheel.write_text("wheel", encoding="utf-8")
        output = self.root / "dist" / "npm-package"

        result = stage_npm_package(self.root, output, wheel)

        manifest = json.loads((output / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "project-kb-cli")
        self.assertEqual(manifest["version"], "1.2.3")
        self.assertNotIn("version", json.loads((self.root / "npm" / "package.template.json").read_text(encoding="utf-8")))
        self.assertEqual(result["version"], "1.2.3")
        self.assertEqual(
            [path.name for path in (output / "vendor").glob("*.whl")],
            ["project_knowledge_cli-1.2.3-py3-none-any.whl"],
        )
        self.assertTrue((output / "bin" / "placeholder.js").exists())
        self.assertTrue((output / "lib" / "placeholder.js").exists())
        self.assertTrue((output / "scripts" / "placeholder.js").exists())
        self.assertEqual((output / "README.md").read_text(encoding="utf-8"), "# Test package\n")
        self.assertEqual(
            (output / "LICENSE").read_text(encoding="utf-8"),
            "Apache-2.0 test license\n",
        )

    def test_stage_rejects_a_wheel_for_another_version_without_replacing_output(self) -> None:
        wheel = self.root / "project_knowledge_cli-1.2.2-py3-none-any.whl"
        wheel.write_text("wheel", encoding="utf-8")
        output = self.root / "dist" / "npm-package"
        output.mkdir(parents=True)
        sentinel = output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "1.2.3"):
            stage_npm_package(self.root, output, wheel)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_stage_rejects_a_second_committed_version_source(self) -> None:
        template = self.root / "npm" / "package.template.json"
        manifest = json.loads(template.read_text(encoding="utf-8"))
        manifest["version"] = "1.2.3"
        template.write_text(json.dumps(manifest), encoding="utf-8")
        wheel = self.root / "project_knowledge_cli-1.2.3-py3-none-any.whl"
        wheel.write_text("wheel", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "must not contain version"):
            stage_npm_package(self.root, self.root / "dist" / "npm-package", wheel)


class NpmReleaseDocumentationTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_readme_documents_the_two_command_npm_path(self) -> None:
        readme = (self.ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("npm install --global project-kb-cli", readme)
        self.assertIn("project-kb init", readme)
        self.assertIn(".codex/config.toml", readme)
        self.assertIn("npm install --global project-kb-cli@latest", readme)
        self.assertIn("project-kb uninstall", readme)
        self.assertIn("npm uninstall --global project-kb-cli", readme)
        self.assertIn("E404", readme)

    def test_compatibility_matrix_records_the_windows_npm_release_gate(self) -> None:
        compatibility = (self.ROOT / "docs" / "compatibility-matrix.md").read_text(encoding="utf-8")
        self.assertIn("Windows 10/11 x64", compatibility)
        self.assertIn("@colbymchenry/codegraph@1.5.0", compatibility)
        self.assertIn("PROJECT_KB_PYTHON", compatibility)

    def test_audit_records_every_npm_bootstrap_requirement(self) -> None:
        audit = (self.ROOT / "docs" / "project-knowledge-system-audit.md").read_text(encoding="utf-8")
        self.assertIn("WP-NPM-01", audit)
        for requirement in range(1, 8):
            self.assertIn(f"NPM-{requirement:03d}", audit)

    def test_windows_ci_runs_the_installed_package_validator(self) -> None:
        workflow = (self.ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
        self.assertIn("npm-bootstrap-windows", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("python scripts/validate_npm_bootstrap.py --json", workflow)


if __name__ == "__main__":
    unittest.main()
