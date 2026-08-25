from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_knowledge.engine import CodeIndexSnapshot
from project_knowledge.progress import ProgressEvent, TerminalProgressRenderer
from project_knowledge.service import ProjectService


class _Engine:
    def initialize(self, root, config):
        return {"initialized": True, "fileCount": 3}

    def snapshot(self, root, config):
        return CodeIndexSnapshot("snapshot", ())


class ProgressTests(unittest.TestCase):
    def test_initialize_reports_ordered_stages_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events: list[ProgressEvent] = []
            service = ProjectService(root, engine_factory=lambda _config: _Engine())
            with (
                patch.object(service, "_atomic_rebuild", return_value={"files_indexed": 0}),
                patch.object(service, "_install_codex_integration", return_value={}),
            ):
                result = service.initialize(progress=events.append)

            self.assertEqual(result["action"], "init")
            self.assertEqual(
                [(event.stage, event.state) for event in events],
                [
                    ("prepare", "started"), ("prepare", "completed"),
                    ("codegraph", "started"), ("codegraph", "completed"),
                    ("snapshot", "started"), ("snapshot", "completed"),
                    ("local_index", "started"), ("local_index", "completed"),
                    ("integration", "started"), ("integration", "completed"),
                ],
            )
            self.assertEqual(events[5].current, 0)

    def test_renderer_is_silent_when_disabled(self) -> None:
        output = io.StringIO()
        renderer = TerminalProgressRenderer(output, enabled=False)
        renderer(ProgressEvent("codegraph", "CodeGraph 建图", "started", 2, 5))
        renderer(ProgressEvent("codegraph", "CodeGraph 建图", "completed", 2, 5))
        self.assertEqual(output.getvalue(), "")

    def test_renderer_finishes_line_on_completion(self) -> None:
        output = io.StringIO()
        renderer = TerminalProgressRenderer(output, enabled=True, start_spinner=False)
        renderer(ProgressEvent("snapshot", "获取代码快照", "started", 3, 5))
        renderer(ProgressEvent("snapshot", "获取代码快照", "completed", 3, 5, current=1401, total=1401))
        renderer.close()
        rendered = output.getvalue()
        self.assertIn("获取代码快照", rendered)
        self.assertIn("1401/1401", rendered)
        self.assertIn("\x1b[2K", rendered)
        self.assertEqual(rendered.count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
