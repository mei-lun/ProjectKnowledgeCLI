import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class MCPStdioEncodingTests(unittest.TestCase):
    def test_legacy_tools_list_has_no_invalid_cache_extension(self):
        from project_knowledge.mcp import MCPServer
        server = object.__new__(MCPServer)
        server._handle_request({'id': 0, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}, None)
        response = server._handle_request({'id': 1, 'method': 'tools/list'}, None)
        self.assertEqual(set(response['result']), {'tools'})

    def test_stdio_is_utf8_even_when_host_python_encoding_is_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(__file__).resolve().parents[1] / 'src')
            env = dict(os.environ, PYTHONPATH=source, PYTHONIOENCODING='cp1252', PYTHONUTF8='0')
            request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}) + '\n'
            result = subprocess.run(
                [sys.executable, '-m', 'project_knowledge', 'mcp', '--project', directory],
                input=request.encode('utf-8'), capture_output=True, env=env, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout.decode('utf-8'))
            self.assertEqual(payload['id'], 1)
            self.assertTrue(any(item['name'] == 'knowledge_task_complete' for item in payload['result']['tools']))


if __name__ == '__main__':
    unittest.main()
