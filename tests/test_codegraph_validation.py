from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate_codegraph_adapter import validate_codegraph


FAKE_CODEGRAPH = r'''
import json
import sys

command = sys.argv[1]
if command == "init":
    print("initialized")
elif command == "status":
    print(json.dumps({"initialized": True, "version": "1.5.0"}))
elif command == "files":
    print(json.dumps([
        {"path": "src/app.py", "language": "python"},
        {"path": "src/helper.py", "language": "python"},
        {"path": "tests/test_app.py", "language": "python"},
        {"path": "service/main.lua", "language": "lua"},
    ]))
elif command == "query":
    print(json.dumps([{"node": {
        "id": "src/app.py::run", "name": "run", "kind": "function",
        "filePath": "src/app.py", "startLine": 3,
    }}]))
elif command == "callers":
    print(json.dumps({"callers": [{
        "id": "tests/test_app.py::test_run", "name": "test_run",
        "filePath": "tests/test_app.py", "startLine": 3,
    }]}))
elif command == "callees":
    print(json.dumps({"callees": [{
        "id": "src/helper.py::helper", "name": "helper",
        "filePath": "src/helper.py", "startLine": 1,
    }]}))
elif command == "impact":
    print(json.dumps({"symbol": "run", "affected": [{
        "id": "src/helper.py::helper", "name": "helper",
        "filePath": "src/helper.py", "startLine": 1,
    }]}))
elif command == "affected":
    print(json.dumps({"affectedTests": ["tests/test_app.py"]}))
else:
    print(json.dumps({}))
'''


class CodeGraphValidationTests(unittest.TestCase):
    def test_validation_uses_temporary_project_and_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_cli = Path(directory) / "fake_codegraph.py"
            fake_cli.write_text(FAKE_CODEGRAPH, encoding="utf-8")
            command = f'"{sys.executable}" "{fake_cli}"'

            report = validate_codegraph(command=command, keep_fixture=False)

        self.assertTrue(report["passed"], report)
        self.assertEqual(report["adapter_version"], "1.5.0")
        self.assertEqual(report["checks"], ["init", "files", "query", "trace", "impact", "affected"])
        self.assertFalse(Path(report["fixture_path"]).exists())


if __name__ == "__main__":
    unittest.main()
