from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_knowledge.service import ProjectService
from project_knowledge.store import KnowledgeStore


class LuaEntrypointKnowledgeTests(unittest.TestCase):
    def test_empty_entrypoint_page_still_has_parser_source_anchors(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "app.py").write_text("def run():" + chr(10) + "    return 1" + chr(10), encoding="utf-8")
        ProjectService(root).initialize()
        with KnowledgeStore(root / ".project-kb" / "index.db", readonly=True) as store:
            record = store.get_knowledge("generated.entrypoints")
        self.assertIsNotNone(record)
        self.assertTrue(record.sources)
        self.assertTrue(any(source.path == "src/project_knowledge/engine.py" for source in record.sources))

    def test_generated_entrypoint_page_is_source_traceable_and_chinese(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "dev").mkdir()
        (root / "dev" / "main.lua").write_text(
            'local skynet = require "skynet"\nskynet.start(function() end)\n',
            encoding="utf-8",
        )
        ProjectService(root).initialize()
        with KnowledgeStore(root / ".project-kb" / "index.db", readonly=True) as store:
            record = store.get_knowledge("generated.entrypoints")
        self.assertIsNotNone(record)
        self.assertIn("Skynet", record.content)
        self.assertIn("dev/main.lua", record.content)
        self.assertTrue(any(source.path == "dev/main.lua" for source in record.sources))


if __name__ == "__main__":
    unittest.main()
