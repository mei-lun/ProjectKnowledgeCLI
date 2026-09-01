from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .codegraph import CodeGraphClient
from .config import ProjectConfig
from .observability import AuditInvocation, MCPAuditLogger, audit_span
from .retrieval import KnowledgeAPI
from .schemas import validate_instance
from .service import ProjectService


CURRENT_PROTOCOL = "2026-07-28"
LEGACY_PROTOCOL = "2025-06-18"


TOOLS: list[dict[str, Any]] = [
    {
        "name": "knowledge_context",
        "title": "Project task context",
        "description": "Return compact, source-traceable project context for a development task.",
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string", "minLength": 1}, "projectPath": {"type": "string", "minLength": 1}, "maxTokens": {"type": "integer", "minimum": 256}, "debug": {"type": "boolean"}},
            "required": ["task"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_search",
        "title": "Search project knowledge",
        "description": "Search generated, draft, curated, and decision knowledge with confidence and freshness.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1}, "kinds": {"type": "array", "items": {"type": "string"}},
                "module": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "projectPath": {"type": "string", "minLength": 1},
                "debug": {"type": "boolean"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_get",
        "title": "Get a knowledge record",
        "description": "Read one stable knowledge record with content, sources, confidence, and freshness.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}, "projectPath": {"type": "string", "minLength": 1}}, "required": ["id"], "additionalProperties": False},
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
                "maxHops": {"type": "integer", "minimum": 0, "maximum": 5},
                "maxRelations": {"type": "integer", "minimum": 1, "maximum": 5000},
                "projectPath": {"type": "string", "minLength": 1},
                "debug": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_status",
        "title": "Project knowledge status",
        "description": "Return index health, pending files, stale knowledge, conflicts, and watcher state.",
        "inputSchema": {"type": "object", "properties": {"projectPath": {"type": "string", "minLength": 1}}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]


WORKFLOW_TOOLS: list[dict[str, Any]] = [
    {
        "name": "knowledge_guidance_plan",
        "title": "规划可生成的类别指导",
        "description": "返回类别目录和当前指导资产状态，供用户选择生成范围。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectPath": {"type": "string", "minLength": 1},
                "selectedCategories": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_task_complete",
        "title": "确认任务完成并准备指导生成",
        "description": "记录用户确认完成的任务，锁定快照并返回受影响类别和证据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectPath": {"type": "string", "minLength": 1},
                "taskId": {"type": "string", "minLength": 1},
                "summary": {"type": "string", "minLength": 1},
                "changedFiles": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "changedSymbols": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "tests": {"type": "array", "items": {"type": "object"}},
                "userConfirmed": {"type": "boolean"},
                "skip": {"type": "boolean"},
                "skipReason": {"type": "string", "minLength": 1},
            },
            "required": ["taskId", "summary", "userConfirmed"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_initialization_start",
        "title": "开始开发指导初始化",
        "description": "基于 CodeGraph 当前快照建立或恢复稳定分批初始化。",
        "inputSchema": {
            "type": "object",
            "properties": {"projectPath": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_initialization_next",
        "title": "读取下一个初始化批次",
        "description": "返回下一个待分析批次、代码事实和按需源码提示。",
        "inputSchema": {
            "type": "object",
            "properties": {"projectPath": {"type": "string", "minLength": 1}, "runId": {"type": "string", "minLength": 1}},
            "required": ["runId"], "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_initialization_submit",
        "title": "提交初始化批次分析",
        "description": "提交 AI 客户端对一个稳定批次归纳的候选类别。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectPath": {"type": "string", "minLength": 1},
                "runId": {"type": "string", "minLength": 1},
                "batchId": {"type": "string", "minLength": 1},
                "snapshotId": {"type": "string", "minLength": 1},
                "candidates": {"type": "array", "items": {"type": "object"}},
                "analyzedFiles": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "error": {"type": "string", "minLength": 1},
            },
            "required": ["runId", "batchId", "snapshotId", "candidates", "analyzedFiles"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_draft_save",
        "title": "保存或拒绝开发指导草稿",
        "description": "保存可见 Markdown 草稿，或记录用户拒绝及原因。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectPath": {"type": "string", "minLength": 1},
                "action": {"enum": ["save", "reject"]},
                "kind": {"enum": ["category_catalog", "methodology", "guidance"]},
                "runId": {"type": "string", "minLength": 1},
                "categoryId": {"type": "string", "minLength": 1},
                "content": {"type": "object"},
                "draftId": {"type": "string", "minLength": 1},
                "reviewer": {"type": "string", "minLength": 1},
                "reviewReason": {"type": "string", "minLength": 1},
            },
            "required": ["action"], "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_draft_confirm",
        "title": "确认开发指导草稿",
        "description": "按草稿 ID 和正文哈希确认分类目录或开发指导。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectPath": {"type": "string", "minLength": 1},
                "draftId": {"type": "string", "minLength": 1},
                "contentHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                "reviewer": {"type": "string", "minLength": 1},
            },
            "required": ["draftId", "contentHash", "reviewer"], "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_changes",
        "title": "读取开发指导变化事实包",
        "description": "比较已处理基线与 CodeGraph 当前快照，返回变化范围和相关指导。",
        "inputSchema": {
            "type": "object",
            "properties": {"projectPath": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "knowledge_update_submit",
        "title": "提交开发指导增量更新",
        "description": "提交事实、指导或分类级更新；需要审核的更新只生成草稿。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectPath": {"type": "string", "minLength": 1},
                "changeId": {"type": "string", "minLength": 1},
                "level": {"enum": ["fact", "guidance", "category"]},
                "categoryId": {"type": "string", "minLength": 1},
                "content": {"type": "object"},
                "evidence": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["changeId", "level"], "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]

TOOLS.extend(WORKFLOW_TOOLS)


class MCPServer:
    def __init__(
        self,
        project: str | Path = ".",
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        *,
        audit_logger: MCPAuditLogger | None = None,
    ):
        self.project = Path(project)
        self.input = input_stream or sys.stdin
        self.output = output_stream or sys.stdout
        self.audit = audit_logger or MCPAuditLogger(
            self.project, protocol_version=CURRENT_PROTOCOL,
        )
        self.api: KnowledgeAPI | None = None
        try:
            with self.audit.activate_session():
                with audit_span(
                    "mcp.server", "knowledge_api_initialize",
                    {"project": self.project.resolve().as_posix()},
                ) as span:
                    self.api = KnowledgeAPI(self.project)
                    span.set_output({"status": "ready"})
        except RuntimeError:
            pass

    def serve(self) -> None:
        try:
            for line in self.input:
                if not line.strip():
                    continue
                invocation: AuditInvocation | None = None
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        invocation = self.audit.begin_message(line, parse_error="JSON-RPC request must be an object")
                        response = self._error(None, -32600, "JSON-RPC request must be an object")
                        request = {"jsonrpc": "2.0", "id": None, "method": "<invalid_request>"}
                        self.audit.start_invocation(invocation, request)
                        self.audit.complete_invocation(invocation, response)
                    else:
                        invocation = self.audit.begin_message(line, request=request)
                        response = self.handle(request, _audit_invocation=invocation)
                except json.JSONDecodeError as error:
                    invocation = self.audit.begin_message(
                        line, parse_error=f"parse error: {error.msg}",
                    )
                    request = {"jsonrpc": "2.0", "id": None, "method": "<parse_error>"}
                    self.audit.start_invocation(invocation, request)
                    response = self._error(None, -32700, f"parse error: {error.msg}")
                    self.audit.complete_invocation(invocation, response, error=error)
                except Exception as error:  # Keep the stdio server alive across tool failures.
                    response = self._error(None, -32603, str(error))
                    if invocation is not None:
                        self.audit.complete_invocation(invocation, response, error=error)
                if response is not None:
                    self.output.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                    self.output.flush()
        finally:
            self.audit.close()

    def handle(
        self,
        request: dict[str, Any],
        *,
        _audit_invocation: AuditInvocation | None = None,
    ) -> dict[str, Any] | None:
        audit = getattr(self, "audit", None)
        invocation = _audit_invocation
        if audit is not None and invocation is None:
            invocation = audit.begin_message(
                json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                request=request,
            )
        request_id = request.get("id")
        if request_id is None:
            if audit is not None and invocation is not None:
                audit.observe_notification(invocation, request)
            return None
        if audit is not None and invocation is not None:
            audit.start_invocation(invocation, request)
        try:
            if audit is not None and invocation is not None:
                with audit.activate(invocation):
                    response = self._handle_request(request, invocation)
            else:
                response = self._handle_request(request, invocation)
        except Exception as error:  # Direct handle calls receive the same stable error as serve().
            response = self._error(request_id, -32603, str(error))
            if audit is not None and invocation is not None:
                audit.complete_invocation(invocation, response, error=error)
            return response
        if audit is not None and invocation is not None:
            audit.complete_invocation(invocation, response)
        return response

    def _handle_request(
        self, request: dict[str, Any], invocation: AuditInvocation | None,
    ) -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if method == "initialize":
            requested = params.get("protocolVersion", LEGACY_PROTOCOL)
            protocol = requested if requested in {CURRENT_PROTOCOL, LEGACY_PROTOCOL, "2025-11-25", "2024-11-05"} else LEGACY_PROTOCOL
            audit = getattr(self, "audit", None)
            if audit is not None and invocation is not None:
                audit.client_initialized(invocation, params, protocol)
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
                tool = next((item for item in TOOLS if item["name"] == name), None)
                if tool is None:
                    raise KeyError(f"unknown tool: {name}")
                if not isinstance(arguments, dict):
                    raise ValueError("tools/call arguments 必须是对象")
                validate_instance(arguments, tool["inputSchema"], "$.arguments")
                with audit_span("tool_dispatch", str(name), arguments) as span:
                    value = self._call(name, arguments)
                    span.set_output(value)
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
        project = Path(project_path).resolve() if project_path else self.project.resolve()
        read_tools = {"knowledge_context", "knowledge_search", "knowledge_get", "knowledge_impact", "knowledge_status"}
        api = None
        if name in read_tools:
            api = KnowledgeAPI(project) if project_path or self.api is None else self.api
            with audit_span("retrieval", name, arguments) as span:
                value, retrieval_trace = self._call_read_tool(api, name, arguments)
                span.set_output({
                    "result": value,
                    "retrieval_trace": retrieval_trace,
                })
                return value

        if name == "knowledge_initialization_start":
            from .initialization import InitializationWorkflow
            return InitializationWorkflow(project).start()
        return self._call_workflow_tool(name, arguments)

    @staticmethod
    def _call_read_tool(
        api: KnowledgeAPI,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if name == "knowledge_context":
            if arguments.get("debug", False):
                value = api.context(
                    str(arguments["task"]), arguments.get("maxTokens"), True,
                )
                return value, value.get("retrieval_trace")
            value, diagnostics = api.context_for_evaluation(
                str(arguments["task"]), arguments.get("maxTokens"),
            )
            return value, diagnostics.get("retrieval_trace")
        if name == "knowledge_search":
            if arguments.get("debug", False):
                value = api.search(
                    str(arguments["query"]), arguments.get("kinds"),
                    arguments.get("module"), int(arguments.get("limit", 10)), True,
                )
                return value, value.get("retrieval_trace")
            value, diagnostics = api.search_for_evaluation(
                str(arguments["query"]), arguments.get("kinds"),
                arguments.get("module"), int(arguments.get("limit", 10)),
            )
            return value, diagnostics.get("retrieval_trace")
        if name == "knowledge_get":
            return api.get(str(arguments["id"])), None
        if name == "knowledge_impact":
            if arguments.get("debug", False):
                value = api.impact(
                    arguments.get("files"), arguments.get("symbols"),
                    arguments.get("maxHops", 1), arguments.get("maxRelations", 500), True,
                )
                return value, value.get("retrieval_trace")
            value, diagnostics = api.impact_for_evaluation(
                arguments.get("files"), arguments.get("symbols"),
                arguments.get("maxHops", 1), arguments.get("maxRelations", 500),
            )
            return value, diagnostics.get("retrieval_trace")
        if name == "knowledge_status":
            return api.status(), None
        raise KeyError(f"unknown read tool: {name}")

    def _call_workflow_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        project_path = arguments.get("projectPath")
        project = Path(project_path).resolve() if project_path else self.project.resolve()
        if name == "knowledge_guidance_plan":
            from .task_workflow import TaskCompletionWorkflow
            return TaskCompletionWorkflow(project).plan(list(arguments.get("selectedCategories", [])))
        if name == "knowledge_task_complete":
            from .task_workflow import TaskCompletionWorkflow
            return TaskCompletionWorkflow(project).complete(
                str(arguments["taskId"]), str(arguments["summary"]),
                changed_files=list(arguments.get("changedFiles", [])),
                changed_symbols=list(arguments.get("changedSymbols", [])),
                tests=list(arguments.get("tests", [])),
                user_confirmed=bool(arguments["userConfirmed"]),
                skip=bool(arguments.get("skip", False)),
                skip_reason=arguments.get("skipReason"),
            )
        if name == "knowledge_initialization_next":
            from .initialization import InitializationWorkflow
            return InitializationWorkflow(project).next_batch(str(arguments["runId"]))
        if name == "knowledge_initialization_submit":
            from .initialization import InitializationWorkflow
            return InitializationWorkflow(project).submit_batch(
                str(arguments["runId"]), str(arguments["batchId"]),
                str(arguments["snapshotId"]), list(arguments["candidates"]),
                analyzed_files=list(arguments["analyzedFiles"]),
                error=arguments.get("error"),
            )
        if name == "knowledge_draft_save":
            from .guidance_workflow import GuidanceWorkflow
            workflow = GuidanceWorkflow(project, client=CodeGraphClient(project, ProjectConfig.load(project)))
            action = arguments["action"]
            if action == "save":
                for field in ("kind", "runId", "content"):
                    if field not in arguments:
                        raise ValueError(f"action=save 缺少字段：{field}")
                return workflow.save_draft(
                    str(arguments["kind"]), str(arguments["runId"]),
                    dict(arguments["content"]), arguments.get("categoryId"),
                )
            if action == "reject":
                for field in ("draftId", "reviewer", "reviewReason"):
                    if field not in arguments:
                        raise ValueError(f"action=reject 缺少字段：{field}")
                return workflow.reject_draft(
                    str(arguments["draftId"]), str(arguments["reviewer"]),
                    str(arguments["reviewReason"]),
                )
            raise ValueError(f"不支持的 action：{action}")
        if name == "knowledge_draft_confirm":
            from .guidance_workflow import GuidanceWorkflow
            return GuidanceWorkflow(
                project, client=CodeGraphClient(project, ProjectConfig.load(project))
            ).confirm_draft(
                str(arguments["draftId"]), str(arguments["contentHash"]),
                str(arguments["reviewer"]),
            )
        if name == "knowledge_changes":
            from .incremental import IncrementalWorkflow
            return IncrementalWorkflow(project).changes()
        if name == "knowledge_update_submit":
            from .incremental import IncrementalWorkflow
            return IncrementalWorkflow(project).submit_update(
                str(arguments["changeId"]), str(arguments["level"]),
                category_id=arguments.get("categoryId"),
                content=dict(arguments.get("content", {})),
                evidence=list(arguments.get("evidence", [])),
            )
        raise KeyError(f"unknown tool: {name}")

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(project: str | Path = ".") -> None:
    audit = MCPAuditLogger(project, protocol_version=CURRENT_PROTOCOL)
    # Connection-time compensation keeps the read tools from serving known old facts.
    try:
        with audit.activate_session():
            with audit_span(
                "mcp.server", "connection_compensation",
                {"task_summary": "MCP connection compensation"},
            ) as span:
                result = ProjectService(project).sync(
                    task_summary="MCP connection compensation",
                )
                span.set_output(result)
    except RuntimeError:
        pass
    try:
        MCPServer(project, audit_logger=audit).serve()
    except BaseException:
        audit.close()
        raise
