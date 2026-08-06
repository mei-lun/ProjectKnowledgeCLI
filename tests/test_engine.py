from __future__ import annotations

import unittest

from project_knowledge.engine import GenericParser, PythonParser


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


if __name__ == "__main__":
    unittest.main()

