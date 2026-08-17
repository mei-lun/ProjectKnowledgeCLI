from __future__ import annotations

import unittest

from project_knowledge.codegraph import CodeGraphEngine
from project_knowledge.config import ProjectConfig
from project_knowledge.engine import CodeIndexEngine, create_engine


class EngineTests(unittest.TestCase):
    def test_factory_only_creates_codegraph_engine(self) -> None:
        engine = create_engine(ProjectConfig(codegraph_command="codegraph"))
        self.assertIsInstance(engine, CodeGraphEngine)

    def test_public_engine_contract_has_no_local_parser_or_entrypoint_api(self) -> None:
        self.assertNotIn("parse", CodeIndexEngine.__dict__)
        self.assertNotIn("discover", CodeIndexEngine.__dict__)
        self.assertNotIn("entrypoints", CodeIndexEngine.__dict__)

    def test_builtin_parser_symbols_are_not_exported(self) -> None:
        import project_knowledge.engine as engine

        for name in ("BuiltinCodeIndexEngine", "PythonParser", "LuaParser", "GenericParser"):
            self.assertFalse(hasattr(engine, name), name)


if __name__ == "__main__":
    unittest.main()
