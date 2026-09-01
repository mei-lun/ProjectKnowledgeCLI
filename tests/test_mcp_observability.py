from __future__ import annotations

import io
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from project_knowledge.cli import main
from project_knowledge.codegraph import CodeGraphClient
from project_knowledge.config import ProjectConfig
from project_knowledge.evidence import EvidencePackBuilder
from project_knowledge import mcp as mcp_module
from project_knowledge.mcp import MCPServer
from project_knowledge.observability import (
    AuditIntegrityError,
    MCPAuditLogger,
    audit_span,
    evaluate_audit_analysis,
    export_audit_log,
    observability_prediction,
    redact_payload,
    validate_audit_log,
)
from project_knowledge.provider import FakeProvider, HttpJsonProvider, ModelRuntime, ProviderConfig
from project_knowledge.schemas import (
    AUDIT_EVENT_SCHEMA,
    MCP_ANALYSIS_SCHEMA,
    all_schemas,
    validate_instance,
)


class MCPObservabilityTests(unittest.TestCase):
    @staticmethod
    def _invocation(logger: MCPAuditLogger, method: str = "tools/call"):
        request = {
            "jsonrpc": "2.0", "id": 1, "method": method,
            "params": {"name": "knowledge_context", "arguments": {"task": "test"}},
        }
        invocation = logger.begin_message(json.dumps(request), request=request)
        logger.start_invocation(invocation, request)
        return invocation

    def test_recursive_redaction_reports_paths_without_truncating_payload(self) -> None:
        private_key = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
        payload = {
            "authorization": "Bearer abcdefghijklmnop",
            "nested": [
                {"api_key": "sk-abcdefghijklmnop", "source": "x" * 20_000},
                "password=hunter2",
                private_key,
            ],
        }

        redacted, findings = redact_payload(payload)

        self.assertEqual(redacted["authorization"], "[REDACTED:authorization]")
        self.assertEqual(redacted["nested"][0]["api_key"], "[REDACTED:api_key]")
        self.assertEqual(redacted["nested"][0]["source"], "x" * 20_000)
        serialized = json.dumps(redacted, ensure_ascii=False)
        self.assertNotIn("hunter2", serialized)
        self.assertNotIn("BEGIN PRIVATE KEY", serialized)
        self.assertEqual(
            {item["path"] for item in findings},
            {"$.authorization", "$.nested[0].api_key", "$.nested[1]", "$.nested[2]"},
        )

    def test_basic_credentials_and_client_request_id_are_always_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            request = {
                "jsonrpc": "2.0",
                "id": "sk-abcdefghijklmnop",
                "method": "ping",
                "params": {"note": "Basic dXNlcjpwYXNzd29yZA=="},
            }
            invocation = logger.begin_message(json.dumps(request), request=request)
            logger.start_invocation(invocation, request)
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": request["id"], "result": {},
            })
            logger.close()

            rendered = logger.log_path.read_text(encoding="utf-8")
            self.assertNotIn("sk-abcdefghijklmnop", rendered)
            self.assertNotIn("dXNlcjpwYXNzd29yZA", rendered)
            self.assertIn("[REDACTED:known_token]", rendered)
            self.assertIn("Basic [REDACTED:basic_credentials]", rendered)

    def test_logger_closes_invocation_and_nested_span_then_exports_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            request = {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "knowledge_context", "arguments": {"task": "save flow"}},
            }
            invocation = logger.begin_message(json.dumps(request), request=request)
            logger.start_invocation(invocation, request)
            with logger.activate(invocation):
                with audit_span("retrieval", "knowledge_context", {"task": "save flow"}) as span:
                    span.set_output({"candidate_count": 2})
            response = {
                "jsonrpc": "2.0",
                "id": 7,
                "result": {
                    "isError": False,
                    "structuredContent": {
                        "files": [{"path": "src/save.py"}],
                        "symbols": [{"id": "src/save.py::save"}],
                        "knowledge": [{"id": "curated.architecture"}],
                        "required_evidence": {"call_paths": [["save", "commit"]]},
                        "extension_points": ["save_hook"],
                        "invariants": ["writes are atomic"],
                        "design_reasons": ["preserve consistency"],
                    },
                },
            }
            logger.complete_invocation(invocation, response)
            logger.close()

            report = validate_audit_log(logger.log_path)
            self.assertTrue(report["valid"], report)
            self.assertEqual(report["invocation_count"], 1)

            output = root / "analysis.jsonl"
            exported = export_audit_log(logger.log_path, output)
            self.assertEqual(exported["record_count"], 1)
            record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["tool"], "knowledge_context")
            self.assertEqual(record["prediction"]["returned_files"], ["src/save.py"])
            self.assertEqual(record["prediction"]["returned_symbols"], ["src/save.py::save"])
            self.assertEqual(record["prediction"]["returned_knowledge_ids"], ["curated.architecture"])
            self.assertEqual(record["prediction"]["call_paths"], [["save", "commit"]])
            self.assertEqual(record["ground_truth_ref"], invocation.invocation_id)
            self.assertEqual(record["causality"], "ordered_only")
            self.assertEqual(record["spans"][0]["kind"], "retrieval")
            self.assertGreaterEqual(record["duration_ms"], 0)
            self.assertTrue(record["started_at"])
            self.assertTrue(record["completed_at"])

            labels = root / "labels.jsonl"
            labels.write_text(json.dumps({
                "schema_version": 1,
                "ground_truth_ref": invocation.invocation_id,
                "expected_files": ["src/save.py"],
                "expected_symbols": ["src/save.py::save"],
                "expected_call_path": ["save", "commit"],
                "expected_extension_points": ["save_hook"],
                "expected_invariants": ["writes are atomic"],
                "expected_design_reasons": ["preserve consistency"],
            }) + "\n", encoding="utf-8")
            quality = evaluate_audit_analysis(output, labels)
            self.assertEqual(quality["metrics"]["file_precision"], 1.0)
            self.assertEqual(quality["metrics"]["file_recall"], 1.0)
            self.assertEqual(quality["metrics"]["symbol_precision"], 1.0)
            self.assertEqual(quality["metrics"]["symbol_recall"], 1.0)
            self.assertEqual(quality["metrics"]["call_path_precision"], 1.0)
            self.assertEqual(quality["metrics"]["call_path_recall"], 1.0)
            self.assertEqual(quality["metrics"]["extension_point_precision"], 1.0)
            self.assertEqual(quality["metrics"]["extension_point_recall"], 1.0)
            self.assertEqual(quality["metrics"]["invariant_precision"], 1.0)
            self.assertEqual(quality["metrics"]["invariant_recall"], 1.0)
            self.assertEqual(quality["metrics"]["design_reason_precision"], 1.0)
            self.assertEqual(quality["metrics"]["design_reason_recall"], 1.0)
            self.assertEqual(quality["label_coverage"], 1.0)

    def test_validation_rejects_missing_terminal_event_and_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            request = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
            invocation = logger.begin_message(json.dumps(request), request=request)
            logger.start_invocation(invocation, request)
            logger.close()

            report = validate_audit_log(logger.log_path)
            self.assertFalse(report["valid"])
            self.assertIn("unclosed_invocation", {item["code"] for item in report["issues"]})
            with self.assertRaises(AuditIntegrityError):
                export_audit_log(logger.log_path, root / "analysis.jsonl")

            with logger.log_path.open("a", encoding="utf-8") as handle:
                handle.write("{broken json\n")
            report = validate_audit_log(logger.log_path)
            self.assertIn("invalid_json", {item["code"] for item in report["issues"]})

    def test_validation_rejects_event_schema_violation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            logger.close()
            events = [json.loads(line) for line in logger.log_path.read_text(encoding="utf-8").splitlines()]
            events[0].pop("payload")
            logger.log_path.write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
            )

            report = validate_audit_log(logger.log_path)
            self.assertFalse(report["valid"])
            self.assertIn("event_schema_invalid", {item["code"] for item in report["issues"]})

    def test_validation_rejects_missing_or_duplicate_invocation_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger)
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1, "result": {},
            })
            logger.close()
            original = [
                json.loads(line)
                for line in logger.log_path.read_text(encoding="utf-8").splitlines()
            ]

            missing = [event for event in original if event["event"] != "invocation_started"]
            logger.log_path.write_text(
                "".join(json.dumps(event) + "\n" for event in missing), encoding="utf-8",
            )
            report = validate_audit_log(logger.log_path)
            self.assertIn(
                "missing_invocation_start", {item["code"] for item in report["issues"]},
            )

            started = next(event for event in original if event["event"] == "invocation_started")
            duplicate = dict(started, event_id="evt_duplicate_start")
            logger.log_path.write_text(
                "".join(json.dumps(event) + "\n" for event in [*original, duplicate]),
                encoding="utf-8",
            )
            report = validate_audit_log(logger.log_path)
            self.assertIn(
                "duplicate_invocation_start", {item["code"] for item in report["issues"]},
            )

    def test_validation_rejects_message_without_invocation_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger, method="ping")
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1, "result": {},
            })
            logger.close()
            events = [
                json.loads(line)
                for line in logger.log_path.read_text(encoding="utf-8").splitlines()
            ]
            next(event for event in events if event["event"] == "message_received")[
                "invocation_id"
            ] = None
            logger.log_path.write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
            )
            report = validate_audit_log(logger.log_path)
            self.assertFalse(report["valid"])
            self.assertIn("missing_invocation_id", {
                item["code"] for item in report["issues"]
            })

    def test_audit_serialization_failure_is_visible_without_changing_business_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger, method="ping")
            with logger.activate(invocation):
                with audit_span("dependency", "bad_payload", {}) as span:
                    span.set_output({"bytes": b"cannot serialize"})
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1, "result": {},
            })
            logger.close()
            report = validate_audit_log(logger.log_path)
            self.assertFalse(report["valid"])
            self.assertIn("audit_gap", {item["code"] for item in report["issues"]})

    def test_validation_rejects_cross_invocation_span_parent_and_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            first = self._invocation(logger)
            with logger.activate(first):
                with audit_span("retrieval", "first", {}) as first_span:
                    first_span.set_output({})
            logger.complete_invocation(first, {"jsonrpc": "2.0", "id": 1, "result": {}})
            second = self._invocation(logger)
            with logger.activate(second):
                with audit_span("retrieval", "second", {}) as second_span:
                    second_span.set_output({})
            logger.complete_invocation(second, {"jsonrpc": "2.0", "id": 1, "result": {}})
            logger.close()
            events = [
                json.loads(line)
                for line in logger.log_path.read_text(encoding="utf-8").splitlines()
            ]
            for event in events:
                if event.get("span_id") == second_span.span_id:
                    event["parent_span_id"] = first_span.span_id
            logger.log_path.write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
            )
            report = validate_audit_log(logger.log_path)
            self.assertIn("cross_invocation_span_parent", {
                item["code"] for item in report["issues"]
            })

            for event in events:
                if event.get("span_id") == first_span.span_id:
                    event["parent_span_id"] = first_span.span_id
            logger.log_path.write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
            )
            report = validate_audit_log(logger.log_path)
            self.assertIn("span_cycle", {item["code"] for item in report["issues"]})

    def test_prediction_uses_tool_specific_result_fields_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            request = {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "knowledge_impact", "arguments": {
                    "files": ["src/input.py"],
                }},
            }
            invocation = logger.begin_message(json.dumps(request), request=request)
            logger.start_invocation(invocation, request)
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1, "result": {
                    "isError": False,
                    "structuredContent": {
                        "input": {"files": ["src/input.py"], "symbols": ["input::symbol"]},
                        "affected_files": ["src/output.py"],
                        "affected_symbols": [{"id": "output::symbol"}],
                        "affected_knowledge": [{"id": "knowledge.output"}],
                    },
                },
            })
            logger.close()
            output = root / "analysis.jsonl"
            export_audit_log(logger.log_path, output)
            prediction = json.loads(output.read_text(encoding="utf-8"))["prediction"]
            self.assertEqual(prediction["returned_files"], ["src/output.py"])
            self.assertEqual(prediction["returned_symbols"], ["output::symbol"])
            self.assertEqual(prediction["returned_knowledge_ids"], ["knowledge.output"])
            self.assertNotIn("src/input.py", prediction["returned_files"])
            self.assertNotIn("input::symbol", prediction["returned_symbols"])

    def test_search_result_paths_and_context_shapes_are_normalized(self) -> None:
        search = {
            "jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {
                "results": [{"id": "knowledge.one", "path": "docs/one.md"}],
            }},
        }
        context = {
            "jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {
                "files": ["src/context.py"],
                "symbols": [{"id": "context::run"}],
                "knowledge": [{"id": "knowledge.context"}],
                "impact": {"call_path": ["context::run", "storage::save"]},
                "extension_points": [{"symbol": "hooks::before_save", "path": "src/hooks.py"}],
            }},
        }
        self.assertEqual(
            observability_prediction(search, "knowledge_search"),
            {
                "returned_files": ["docs/one.md"],
                "returned_symbols": [],
                "returned_knowledge_ids": ["knowledge.one"],
                "call_paths": [],
                "extension_points": [],
                "invariants": [],
                "design_reasons": [],
            },
        )
        prediction = observability_prediction(context, "knowledge_context")
        self.assertEqual(prediction["call_paths"], [["context::run", "storage::save"]])
        self.assertEqual(prediction["extension_points"], ["hooks::before_save"])

    def test_non_debug_context_keeps_full_retrieval_trace_only_in_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = MCPServer(root, io.StringIO(), io.StringIO())
            server.api = Mock()
            server.api.context_for_evaluation.return_value = (
                {"files": ["src/a.py"]},
                {"retrieval_trace": {"stages": {"ranking": {"candidate_count": 3}}}},
            )
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "knowledge_context", "arguments": {"task": "task"}},
            })
            server.audit.close()

            self.assertNotIn("retrieval_trace", response["result"]["structuredContent"])
            events = [
                json.loads(line)
                for line in server.audit.log_path.read_text(encoding="utf-8").splitlines()
            ]
            completed = next(
                event for event in events
                if event["event"] == "span_completed"
                and event["payload"]["kind"] == "retrieval"
            )
            self.assertEqual(
                completed["payload"]["output"]["retrieval_trace"]["stages"]["ranking"]["candidate_count"],
                3,
            )

    def test_connection_compensation_and_api_initialization_are_session_spans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = Mock()
            service.sync.return_value = {"status": "aligned"}
            with patch("project_knowledge.mcp.ProjectService", return_value=service), patch(
                "project_knowledge.mcp.KnowledgeAPI", return_value=Mock(),
            ), patch("sys.stdin", io.StringIO()), patch("sys.stdout", io.StringIO()):
                mcp_module.serve(root)

            events = [
                json.loads(line)
                for line in (root / ".project-kb" / "logs" / "mcp-events.jsonl")
                .read_text(encoding="utf-8").splitlines()
            ]
            starts = {
                event["payload"]["name"]: event
                for event in events if event["event"] == "span_started"
            }
            self.assertIn("connection_compensation", starts)
            self.assertIn("knowledge_api_initialize", starts)
            self.assertIsNone(starts["connection_compensation"]["invocation_id"])
            self.assertEqual(
                starts["connection_compensation"]["session_id"],
                starts["knowledge_api_initialize"]["session_id"],
            )
            self.assertTrue(validate_audit_log(
                root / ".project-kb" / "logs" / "mcp-events.jsonl",
            )["valid"])

    def test_export_span_order_is_independent_of_raw_event_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger)
            with logger.activate(invocation):
                with audit_span("retrieval", "first", {}) as first:
                    first.set_output({"position": 1})
                with audit_span("retrieval", "second", {}) as second:
                    second.set_output({"position": 2})
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1,
                "result": {"isError": False, "structuredContent": {}},
            })
            logger.close()
            first_output = root / "first.jsonl"
            second_output = root / "second.jsonl"
            export_audit_log(logger.log_path, first_output)
            lines = logger.log_path.read_text(encoding="utf-8").splitlines()
            logger.log_path.write_text(
                "\n".join(reversed(lines)) + "\n", encoding="utf-8",
            )
            export_audit_log(logger.log_path, second_output)
            self.assertEqual(
                first_output.read_bytes(), second_output.read_bytes(),
            )

    def test_client_correlation_requires_explicit_trace_or_task_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            for request_id, metadata in (
                (1, {"progressToken": "p1"}),
                (2, {"traceId": "trace-2"}),
            ):
                request = {
                    "jsonrpc": "2.0", "id": request_id, "method": "ping",
                    "params": {"_meta": metadata},
                }
                invocation = logger.begin_message(json.dumps(request), request=request)
                logger.start_invocation(invocation, request)
                logger.complete_invocation(invocation, {
                    "jsonrpc": "2.0", "id": request_id, "result": {},
                })
            logger.close()
            output = root / "analysis.jsonl"
            export_audit_log(logger.log_path, output)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [record["causality"] for record in records],
                ["ordered_only", "client_correlated"],
            )

    def test_evaluation_omits_unlabeled_dimensions_and_rejects_malformed_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger, method="ping")
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1, "result": {},
            })
            logger.close()
            analysis = root / "analysis.jsonl"
            export_audit_log(logger.log_path, analysis)
            labels = root / "labels.jsonl"
            labels.write_text(json.dumps({
                "ground_truth_ref": invocation.invocation_id,
                "expected_files": [], "expected_symbols": [],
                "expected_call_path": [],
            }) + "\n", encoding="utf-8")
            quality = evaluate_audit_analysis(analysis, labels)
            self.assertNotIn("file_precision", quality["metrics"])
            self.assertIn("file", quality["not_applicable_dimensions"])
            labels.write_text(json.dumps({
                "ground_truth_ref": invocation.invocation_id,
                "expected_files": "src/a.py",
            }) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                evaluate_audit_analysis(analysis, labels)

    def test_stdio_server_records_protocol_messages_errors_notifications_and_exact_responses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lines = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2026-07-28",
                    "clientInfo": {"name": "audit-test", "version": "1"},
                }},
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {
                    "token": "sk-abcdefghijklmnop",
                }},
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "missing_tool", "arguments": {},
                }},
            ]
            input_text = "\n".join(json.dumps(item) for item in lines) + "\n{not-json\n"
            output = io.StringIO()
            server = MCPServer(root, io.StringIO(input_text), output)

            server.serve()

            responses = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(responses), 3)
            self.assertEqual(responses[0]["id"], 1)
            self.assertTrue(responses[1]["result"]["isError"])
            self.assertEqual(responses[2]["error"]["code"], -32700)

            log_path = root / ".project-kb" / "logs" / "mcp-events.jsonl"
            report = validate_audit_log(log_path)
            self.assertTrue(report["valid"], report)
            self.assertEqual(report["message_count"], 4)
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len([event for event in events if event["event"] == "message_received"]), 4)
            self.assertTrue(any(event["event"] == "notification_observed" for event in events))
            self.assertNotIn("sk-abcdefghijklmnop", log_path.read_text(encoding="utf-8"))
            logged_responses = [
                event["payload"]["response"]
                for event in events
                if event["event"] in {"invocation_completed", "invocation_failed"}
            ]
            self.assertEqual(logged_responses, responses)
            terminal_events = [
                event for event in events
                if event["event"] in {"invocation_completed", "invocation_failed"}
            ]
            self.assertTrue(all(event["payload"]["duration_ms"] >= 0 for event in terminal_events))
            for event, response in zip(terminal_events, responses, strict=True):
                wire = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.assertEqual(
                    event["payload"]["response_sha256"],
                    "sha256:" + hashlib.sha256(wire).hexdigest(),
                )

    def test_non_object_json_is_audited_as_invalid_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            MCPServer(root, io.StringIO("[]\n"), output).serve()

            response = json.loads(output.getvalue())
            self.assertEqual(response["error"]["code"], -32600)
            report = validate_audit_log(root / ".project-kb" / "logs" / "mcp-events.jsonl")
            self.assertTrue(report["valid"], report)
            self.assertEqual(report["message_count"], 1)

    def test_read_tool_has_retrieval_span_nested_below_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = MCPServer(root, io.StringIO(), io.StringIO())
            server.api = Mock()
            server.api.status.return_value = {"initialized": True}

            response = server.handle({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "knowledge_status", "arguments": {}},
            })
            server.audit.close()

            self.assertFalse(response["result"]["isError"])
            events = [json.loads(line) for line in server.audit.log_path.read_text(encoding="utf-8").splitlines()]
            starts = [
                event for event in events
                if event["event"] == "span_started" and event["invocation_id"]
            ]
            self.assertEqual(
                [event["payload"]["kind"] for event in starts],
                ["tool_dispatch", "retrieval"],
            )
            self.assertEqual(starts[1]["parent_span_id"], starts[0]["span_id"])

    def test_codegraph_execution_and_request_cache_are_distinct_child_spans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger)
            runner = Mock(return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"initialized": True, "version": "1.5.0"}), "",
            ))
            client = CodeGraphClient(
                root,
                ProjectConfig(codegraph_command=sys.executable),
                runner=runner,
            )

            with logger.activate(invocation):
                with audit_span("tool_dispatch", "knowledge_status", {}) as parent:
                    with client.request_scope():
                        first = client.status()
                        second = client.status()
                    parent.set_output(second)
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1,
                "result": {"isError": False, "structuredContent": first},
            })
            logger.close()

            self.assertEqual(runner.call_count, 1)
            events = [json.loads(line) for line in logger.log_path.read_text(encoding="utf-8").splitlines()]
            starts = [event for event in events if event["event"] == "span_started"]
            self.assertEqual(
                [event["payload"]["kind"] for event in starts],
                ["tool_dispatch", "dependency.codegraph", "dependency.codegraph_cache"],
            )
            self.assertEqual(starts[1]["parent_span_id"], starts[0]["span_id"])
            self.assertEqual(starts[2]["parent_span_id"], starts[0]["span_id"])
            codegraph_end = next(
                event for event in events
                if event["event"] == "span_completed"
                and event["payload"]["kind"] == "dependency.codegraph"
            )
            self.assertEqual(codegraph_end["payload"]["output"]["returncode"], 0)
            self.assertIn('"initialized": true', codegraph_end["payload"]["output"]["stdout"])

    def test_provider_generation_and_cache_hit_are_audited_without_changing_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("def run(): return 1\n", encoding="utf-8")
            pack = EvidencePackBuilder(root).build("generate", ["src/a.py"])
            config = ProviderConfig(
                provider_id="fake", model_id="fake-v1", enabled=True,
                prompt_version="feature-guide-v1", output_schema_version="draft-v1",
            )
            runtime = ModelRuntime(
                root, FakeProvider(config, {"title": "draft", "status": "draft"}), config,
            )
            schema = {
                "type": "object", "required": ["title", "status"],
                "properties": {"title": {"type": "string"}, "status": {"enum": ["draft"]}},
                "additionalProperties": False,
            }
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger)

            with logger.activate(invocation):
                first = runtime.generate(pack, schema)
                second = runtime.generate(pack, schema)
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1,
                "result": {"isError": False, "structuredContent": second.to_dict()},
            })
            logger.close()

            self.assertFalse(first.cached)
            self.assertTrue(second.cached)
            events = [json.loads(line) for line in logger.log_path.read_text(encoding="utf-8").splitlines()]
            kinds = [
                event["payload"]["kind"] for event in events
                if event["event"] == "span_started"
            ]
            self.assertEqual(kinds.count("dependency.provider"), 1)
            self.assertEqual(kinds.count("dependency.provider_cache"), 1)

    def test_http_provider_records_each_retry_and_redacts_authorization_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempts: list[dict[str, object]] = []

            def transport(endpoint, payload, headers, timeout):
                attempts.append({"endpoint": endpoint, "payload": payload, "headers": headers})
                if len(attempts) == 1:
                    raise TimeoutError("temporary")
                return {"output": {"ok": True}, "usage": {"input_tokens": 2}}

            config = ProviderConfig(
                provider_id="http-json", model_id="local-v1",
                endpoint="http://127.0.0.1:11434/generate",
                enabled=True, allow_network=True, local_only=True,
                api_key_env="OBS_TEST_API_KEY", max_retries=1,
            )
            provider = HttpJsonProvider(config, transport=transport)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger)

            with patch.dict(os.environ, {"OBS_TEST_API_KEY": "sk-abcdefghijklmnop"}):
                with logger.activate(invocation):
                    result = provider.generate_structured({"task": "test"}, threading.Event())
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1,
                "result": {"isError": False, "structuredContent": result.output},
            })
            logger.close()

            self.assertEqual(result.output, {"ok": True})
            self.assertEqual(len(attempts), 2)
            content = logger.log_path.read_text(encoding="utf-8")
            self.assertNotIn("sk-abcdefghijklmnop", content)
            events = [json.loads(line) for line in content.splitlines()]
            starts = [
                event for event in events
                if event["event"] == "span_started"
                and event["payload"]["kind"] == "dependency.provider_attempt"
            ]
            self.assertEqual(len(starts), 2)
            self.assertEqual(starts[0]["payload"]["input"]["attempt"], 1)
            self.assertEqual(starts[1]["payload"]["input"]["attempt"], 2)
            self.assertEqual(starts[0]["payload"]["input"]["headers"]["Authorization"], "[REDACTED:authorization]")
            terminals = [
                event for event in events
                if event["span_id"] in {item["span_id"] for item in starts}
                and event["event"] in {"span_completed", "span_failed"}
            ]
            self.assertEqual([event["event"] for event in terminals], ["span_failed", "span_completed"])

    def test_mcp_log_validate_and_export_cli_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger, method="ping")
            response = {"jsonrpc": "2.0", "id": 1, "result": {}}
            logger.complete_invocation(invocation, response)
            logger.close()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "mcp-log", "validate", "--project", str(root), "--json",
                ])
            self.assertEqual(exit_code, 0)
            self.assertTrue(json.loads(stdout.getvalue())["valid"])

            analysis = root / "analysis.jsonl"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "mcp-log", "export", "--project", str(root),
                    "--output", str(analysis), "--json",
                ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["record_count"], 1)
            self.assertTrue(analysis.exists())

            record = json.loads(analysis.read_text(encoding="utf-8"))
            labels = root / "labels.jsonl"
            labels.write_text(json.dumps({
                "schema_version": 1,
                "ground_truth_ref": record["ground_truth_ref"],
                "expected_files": [], "expected_symbols": [], "expected_call_path": [],
            }) + "\n", encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "mcp-log", "evaluate", "--analysis", str(analysis),
                    "--ground-truth", str(labels), "--json",
                ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["evaluated_count"], 1)

        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([
                    "mcp-log", "validate", "--project", directory, "--json",
                ])
            self.assertEqual(exit_code, 2)
            self.assertEqual(
                {item["code"] for item in json.loads(stdout.getvalue())["issues"]},
                {"no_audit_log"},
            )

    def test_raw_events_and_analysis_rows_publish_machine_validatable_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger)
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1,
                "result": {"isError": False, "structuredContent": {}},
            })
            logger.close()
            events = [json.loads(line) for line in logger.log_path.read_text(encoding="utf-8").splitlines()]
            for event in events:
                validate_instance(event, AUDIT_EVENT_SCHEMA)
            output = root / "analysis.jsonl"
            export_audit_log(logger.log_path, output)
            validate_instance(
                json.loads(output.read_text(encoding="utf-8").splitlines()[0]),
                MCP_ANALYSIS_SCHEMA,
            )
            schemas = all_schemas()
            self.assertIs(schemas["audit-event-v1.json"], AUDIT_EVENT_SCHEMA)
            self.assertIs(schemas["mcp-analysis-v1.json"], MCP_ANALYSIS_SCHEMA)

    def test_concurrent_sessions_append_without_corrupting_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write_session(index: int) -> None:
                logger = MCPAuditLogger(root)
                request = {"jsonrpc": "2.0", "id": index, "method": "ping", "params": {}}
                invocation = logger.begin_message(json.dumps(request), request=request)
                logger.start_invocation(invocation, request)
                logger.complete_invocation(invocation, {
                    "jsonrpc": "2.0", "id": index, "result": {},
                })
                logger.close()

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(write_session, range(8)))

            report = validate_audit_log(root / ".project-kb" / "logs" / "mcp-events.jsonl")
            self.assertTrue(report["valid"], report)
            self.assertEqual(report["session_count"], 8)
            self.assertEqual(report["invocation_count"], 8)

    def test_independent_processes_append_without_jsonl_tearing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = (
                "import json, sys; "
                "from project_knowledge.observability import MCPAuditLogger; "
                "from pathlib import Path; "
                "root=Path(sys.argv[1]); logger=MCPAuditLogger(root); "
                "request={'jsonrpc':'2.0','id':int(sys.argv[2]),'method':'ping','params':{}}; "
                "inv=logger.begin_message(json.dumps(request), request=request); "
                "logger.start_invocation(inv, request); "
                "logger.complete_invocation(inv, {'jsonrpc':'2.0','id':int(sys.argv[2]),'result':{}}); "
                "logger.close()"
            )
            processes = [
                subprocess.Popen([sys.executable, "-c", code, str(root), str(index)])
                for index in range(4)
            ]
            self.assertEqual([process.wait() for process in processes], [0] * 4)
            report = validate_audit_log(root / ".project-kb" / "logs" / "mcp-events.jsonl")
            self.assertTrue(report["valid"], report)
            self.assertEqual(report["session_count"], 4)
            self.assertEqual(report["invocation_count"], 4)

    def test_recovered_write_failure_emits_gap_and_blocks_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            original_write = logger._write
            calls = 0

            def flaky_write(record):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("disk unavailable")
                original_write(record)

            logger._write = flaky_write
            invocation = self._invocation(logger, method="ping")
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1, "result": {},
            })
            logger.close()

            report = validate_audit_log(logger.log_path)
            self.assertFalse(report["valid"])
            self.assertIn("audit_gap", {item["code"] for item in report["issues"]})
            with self.assertRaises(AuditIntegrityError):
                export_audit_log(logger.log_path, root / "analysis.jsonl")

    def test_codegraph_failure_span_keeps_exit_code_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logger = MCPAuditLogger(root)
            invocation = self._invocation(logger)
            runner = Mock(return_value=subprocess.CompletedProcess(
                [], 7, "partial output", "command failed",
            ))
            client = CodeGraphClient(
                root, ProjectConfig(codegraph_command=sys.executable), runner=runner,
            )

            with logger.activate(invocation):
                with self.assertRaisesRegex(Exception, "退出码 7"):
                    client.status()
            logger.complete_invocation(invocation, {
                "jsonrpc": "2.0", "id": 1,
                "result": {"isError": True, "content": [{"type": "text", "text": "failed"}]},
            })
            logger.close()

            events = [json.loads(line) for line in logger.log_path.read_text(encoding="utf-8").splitlines()]
            failed = next(
                event for event in events
                if event["event"] == "span_failed"
                and event["payload"]["kind"] == "dependency.codegraph"
            )
            self.assertEqual(failed["payload"]["output"]["returncode"], 7)
            self.assertEqual(failed["payload"]["output"]["stdout"], "partial output")
            self.assertEqual(failed["payload"]["output"]["stderr"], "command failed")


if __name__ == "__main__":
    unittest.main()
