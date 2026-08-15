from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from project_knowledge.config import ProjectConfig
from project_knowledge.codegraph import CodeGraphEngine
from project_knowledge.engine import GenericParser, PythonParser, create_engine


class EngineTests(unittest.TestCase):
    def test_python_ast_extracts_symbols_calls_inheritance_and_routes(self) -> None:
        source = '''
from flask import Flask

class Child(Base):
    def work(self, value):
        return helper(value)

@app.post("/items")
def create_item():
    return Child().work(1)
'''
        result = PythonParser("src/app.py", source).parse()
        ids = {symbol.id for symbol in result.symbols}
        self.assertIn("src/app.py::Child", ids)
        self.assertIn("src/app.py::Child.work", ids)
        self.assertIn("src/app.py::create_item", ids)
        self.assertTrue(any(relation.kind == "inherits" and relation.target == "Base" for relation in result.relations))
        self.assertTrue(any(relation.kind == "calls" and relation.target == "helper" for relation in result.relations))
        self.assertEqual([(route.method, route.route) for route in result.routes], [("POST", "/items")])

    def test_invalid_python_reports_parse_error(self) -> None:
        result = PythonParser("broken.py", "def broken(:\n").parse()
        self.assertIsNotNone(result.parse_error)

    def test_generic_parser_marks_relations_low_confidence(self) -> None:
        result = GenericParser(
            "src/server.ts",
            'import x from "pkg";\nexport function run() { helper(); }\napp.get("/health", run);',
            "TypeScript",
        ).parse()
        self.assertTrue(any(symbol.name == "run" for symbol in result.symbols))
        self.assertTrue(any(route.route == "/health" for route in result.routes))
        self.assertTrue(all(relation.confidence < 1 for relation in result.relations))

    def test_codegraph_engine_is_available_as_a_public_adapter(self) -> None:
        engine = create_engine(ProjectConfig(engine="codegraph", codegraph_command="/usr/bin/codegraph"))
        self.assertIsInstance(engine, CodeGraphEngine)

    def test_generic_parser_disambiguates_repeated_lua_function_ids(self) -> None:
        result = GenericParser(
            "service/player.lua",
            "function handler.run() return 1 end\nfunction handler.run() return 2 end\n",
            "Lua",
        ).parse()
        ids = [symbol.id for symbol in result.symbols]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("service/player.lua::handler.run", ids)
        self.assertIn("service/player.lua::handler.run@2", ids)

    def test_python_parser_disambiguates_repeated_definition_ids(self) -> None:
        result = PythonParser(
            "src/redefined.py",
            "def feature():\n    return 1\n\ndef feature():\n    return 2\n",
        ).parse()
        ids = [symbol.id for symbol in result.symbols]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("src/redefined.py::feature", ids)
        self.assertIn("src/redefined.py::feature@4", ids)

    def test_discovery_excludes_evaluation_outputs_to_prevent_self_contamination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "evaluation" / "reports").mkdir(parents=True)
            (root / "evaluation" / "baselines").mkdir(parents=True)
            (root / "src" / "app.py").write_text("def app(): pass\n", encoding="utf-8")
            (root / "evaluation" / "reports" / "latest.json").write_text("{}\n", encoding="utf-8")
            (root / "evaluation" / "baselines" / "base.json").write_text("{}\n", encoding="utf-8")

            paths = {item.path for item in create_engine(ProjectConfig()).discover(root, ProjectConfig())}

            self.assertIn("src/app.py", paths)
            self.assertNotIn("evaluation/reports/latest.json", paths)
            self.assertNotIn("evaluation/baselines/base.json", paths)

    def test_discovery_excludes_internal_git_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / ".worktrees" / "feature" / "src").mkdir(parents=True)
            (root / "src" / "app.py").write_text("def app(): pass\n", encoding="utf-8")
            (root / ".worktrees" / "feature" / "src" / "duplicate.py").write_text(
                "def duplicate(): pass\n",
                encoding="utf-8",
            )

            config = ProjectConfig()
            paths = {item.path for item in create_engine(config).discover(root, config)}

            self.assertIn("src/app.py", paths)
            self.assertNotIn(".worktrees/feature/src/duplicate.py", paths)


if __name__ == "__main__":
    unittest.main()
