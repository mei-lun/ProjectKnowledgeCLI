from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .retrieval import KnowledgeAPI


CURRENT_PROTOCOL = "2026-07-28"
LEGACY_PROTOCOL = "2025-06-18"


TOOLS: list[dict[str, Any]] = [
    {
        "name": "knowledge_context",
        "title": "Project task context",
        "description": "Return compact, source-traceable project context for a development task.",
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string"}, "projectPath": {"type": "string"}, "maxTokens": {"type": "integer", "minimum": 256}},
            "required": ["task"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_search",
        "title": "Search project knowledge",
        "description": "Search generated, curated, and decision knowledge with confidence and freshness.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}, "kinds": {"type": "array", "items": {"type": "string"}},
                "module": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_get",
        "title": "Get a knowledge record",
        "description": "Read one stable knowledge record with content, sources, confidence, and freshness.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_impact",
        "title": "Analyze project impact",
        "description": "Find modules, symbols, tests, and knowledge affected by files or symbols.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_status",
        "title": "Project knowledge status",
        "description": "Return index health, pending files, stale knowledge, conflicts, and watcher state.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]


class MCPServer:
    def __init__(self, project: str | Path = ".", input_stream: TextIO | None = None, output_stream: TextIO | None = None):
        self.project = Path(project)
        self.input = input_stream or sys.stdin
        self.output = output_stream or sys.stdout
        self.api = KnowledgeAPI(self.project)

    def serve(self) -> None:
        for line in self.input:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                response = self.handle(request)
            except json.JSONDecodeError as error:
                response = self._error(None, -32700, f"parse error: {error.msg}")
            except Exception as error:  # Keep the stdio server alive across tool failures.
                response = self._error(None, -32603, str(error))
            if response is not None:
                self.output.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.output.flush()

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if request_id is None:
            return None
        if method == "initialize":
            requested = params.get("protocolVersion", LEGACY_PROTOCOL)
            protocol = requested if requested in {CURRENT_PROTOCOL, LEGACY_PROTOCOL, "2025-11-25", "2024-11-05"} else LEGACY_PROTOCOL
            return self._result(request_id, {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "project-knowledge", "title": "Project Knowledge", "version": __version__},
                "instructions": "Call knowledge_status, then knowledge_context. Verify stale records in live source.",
            })
        if method == "server/discover":
            return self._result(request_id, {
                "protocolVersion": CURRENT_PROTOCOL,
                "supportedProtocolVersions": [CURRENT_PROTOCOL, "2025-11-25", LEGACY_PROTOCOL, "2024-11-05"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "project-knowledge", "title": "Project Knowledge", "version": __version__},
            })
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": TOOLS, "ttlMs": 60_000, "cacheScope": "server"})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                value = self._call(name, arguments)
            except (KeyError, ValueError, RuntimeError) as error:
                result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
                return self._result(request_id, result)
            text = json.dumps(value, ensure_ascii=False, indent=2)
            return self._result(request_id, {
                "content": [{"type": "text", "text": text}],
                "structuredContent": value,
                "isError": False,
            })
        return self._error(request_id, -32601, f"method not found: {method}")

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        project_path = arguments.get("projectPath")
        api = KnowledgeAPI(project_path) if project_path else self.api
        if name == "knowledge_context":
            return api.context(str(arguments["task"]), arguments.get("maxTokens"))
        if name == "knowledge_search":
            return api.search(str(arguments["query"]), arguments.get("kinds"), arguments.get("module"), int(arguments.get("limit", 10)))
        if name == "knowledge_get":
            return api.get(str(arguments["id"]))
        if name == "knowledge_impact":
            return api.impact(arguments.get("files"), arguments.get("symbols"))
        if name == "knowledge_status":
            return api.status()
        raise KeyError(f"unknown tool: {name}")

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(project: str | Path = ".") -> None:
    # Connection-time compensation keeps the read tools from serving known old facts.
    service = __import__("project_knowledge.service", fromlist=["ProjectService"]).ProjectService(project)
    try:
        service.sync(task_summary="MCP connection compensation")
    except RuntimeError:
        pass
    MCPServer(project).serve()

