from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_knowledge.config import ProjectConfig
from project_knowledge.service import ProjectService


class SingleDirectoryTests(unittest.TestCase):
    def test_new_projects_default_all_knowledge_paths_under_project_kb(self) -> None:
        config = ProjectConfig(project_name="gardenserver")
        self.assertEqual(config.knowledge_root, ".project-kb")
        self.assertEqual(config.generated_root, ".project-kb/generated")
        self.assertEqual(config.drafts_root, ".project-kb/drafts")
        self.assertEqual(config.curated_root, ".project-kb/curated")
        self.assertEqual(config.decisions_root, ".project-kb/decisions")

    def test_initialize_dry_run_does_not_plan_knowledge_outside_project_kb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "main.lua").write_text("function main() end\n", encoding="utf-8")
            result = ProjectService(root).initialize(dry_run=True)
            planned = set(result["files_to_create"])
            self.assertTrue(planned)
            self.assertTrue(all(path == ".project-kb.yml" or path.startswith(".project-kb/") for path in planned))
            self.assertFalse(any(path.startswith("docs/") for path in planned))


if __name__ == "__main__":
    unittest.main()
