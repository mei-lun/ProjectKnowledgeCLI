from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_knowledge.config import ProjectConfig
from project_knowledge.engine import BuiltinCodeIndexEngine, LuaParser


class EngineContractAndLuaTests(unittest.TestCase):
    def _workspace(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "dev" / "player").mkdir(parents=True)
        (root / "service").mkdir()
        (root / "tests").mkdir()
        (root / ".svn").mkdir()
        (root / "log").mkdir()
        (root / "dev" / "player" / "main.lua").write_text(
            '''local skynet = require "skynet"
local cluster = require("skynet.cluster")
local M = {}
function M.init()
    skynet.start(function() end)
    local service = skynet.newservice("service.player")
    skynet.name(".player", service)
    skynet.call(service, "lua", "get")
    skynet.send(service, "lua", "set")
    cluster.proxy("center")
    cluster.call("center", "player", "get")
    cluster.send("center", "player", "set")
    skynet.dispatch("lua", function() end)
end
function M:update(value)
    return value
end
return M
''',
            encoding="utf-8",
        )
        (root / "service" / "player.lua").write_text(
            'local skynet = require("skynet")\nfunction CMD.run() return skynet.uniqueservice("service.db") end\n',
            encoding="utf-8",
        )
        (root / "dev" / "player" / "schema.sql").write_text(
            "CREATE TABLE player (id INTEGER PRIMARY KEY, name TEXT);\n",
            encoding="utf-8",
        )
        (root / "config").mkdir()
        (root / "config" / "main.conf").write_text(
            'daemon = "skynet"\nport = 8888\n',
            encoding="utf-8",
        )
        (root / "tests" / "test_player.lua").write_text("function test_player() end\n", encoding="utf-8")
        (root / "log" / "ignored.lua").write_text("function ignored() end\n", encoding="utf-8")
        (root / ".svn" / "entries").write_text("ignored\n", encoding="utf-8")
        return temporary, root, ProjectConfig(project_name="fixture", exclude=[".svn/**", "log/**"])

    def test_public_engine_contract_and_queries(self) -> None:
        temporary, root, config = self._workspace()
        self.addCleanup(temporary.cleanup)
        engine = BuiltinCodeIndexEngine()
        for name in ["initialize", "sync", "search_symbols", "get_source", "trace", "impact", "affected_tests"]:
            self.assertTrue(callable(getattr(engine, name)), name)
        summary = engine.initialize(root, config)
        self.assertGreaterEqual(summary["files"], 4)
        matches = engine.search_symbols(root, config, "M.update")
        self.assertTrue(any("M.update" in item.id or item.name == "update" for item in matches))
        source = engine.get_source(root, "dev/player/main.lua", 1, 2)
        self.assertIn('require "skynet"', source)
        trace = engine.trace(root, "dev/player/main.lua::M.init", config, max_depth=1)
        self.assertTrue(trace)
        impact = engine.impact(root, config, symbols=["dev/player/main.lua::M.init"], max_hops=2, max_relations=50)
        self.assertIn("relations", impact)
        self.assertIn("dev/player/main.lua", impact["affected_files"])
        self.assertIn("tests/test_player.lua", engine.affected_tests(root, config, impact["affected_files"]))
        self.assertNotIn(".svn/entries", {item.path for item in engine.discover(root, config)})
        self.assertNotIn("log/ignored.lua", {item.path for item in engine.discover(root, config)})

    def test_lua_skynet_semantics_and_config_sql(self) -> None:
        temporary, root, config = self._workspace()
        self.addCleanup(temporary.cleanup)
        engine = BuiltinCodeIndexEngine()
        lua = next(result for item, result in engine._parse_workspace(root, config) if item.path == "dev/player/main.lua")
        symbol_names = {symbol.name for symbol in lua.symbols}
        self.assertIn("init", symbol_names)
        self.assertIn("update", symbol_names)
        kinds = {relation.kind for relation in lua.relations}
        for expected in ["imports", "service_start", "service_create", "service_name", "skynet_call", "skynet_send", "cluster_call", "cluster_send", "dispatch"]:
            self.assertIn(expected, kinds, expected)
        config_result = engine.parse(root, next(item for item in engine.discover(root, config) if item.path == "config/main.conf"))
        self.assertEqual(config_result.parser, "config")
        self.assertIn("daemon", {symbol.name for symbol in config_result.symbols})
        sql_result = engine.parse(root, next(item for item in engine.discover(root, config) if item.path.endswith("schema.sql")))
        self.assertIn("player", {symbol.name for symbol in sql_result.symbols})
        self.assertEqual(sql_result.parser, "sql")

    def test_lua_parser_handles_require_forms_and_duplicate_symbols(self) -> None:
        source = '''local a = require "foo.bar"
local b = require("baz")
function module.name() end
function module.name() end
function obj:run() end
'''
        result = LuaParser("dev/x.lua", source).parse()
        self.assertEqual(result.parser, "lua-skynet")
        ids = {symbol.id for symbol in result.symbols}
        self.assertIn("dev/x.lua::module.name", ids)
        self.assertIn("dev/x.lua::module.name@4", ids)
        self.assertIn("dev/x.lua::obj:run", ids)
        imports = [r.target for r in result.relations if r.kind == "imports"]
        self.assertIn("foo.bar", imports)
        self.assertIn("baz", imports)


if __name__ == "__main__":
    unittest.main()
