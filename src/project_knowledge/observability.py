from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .evidence import SecretScanner
from .schemas import (
    AUDIT_EVENT_SCHEMA,
    MCP_ANALYSIS_SCHEMA,
    SchemaValidationError,
    validate_instance,
)
from .util import atomic_write, process_alive, utc_now


AUDIT_SCHEMA_VERSION = 1
ANALYSIS_SCHEMA_VERSION = 1
TERMINAL_EVENTS = {
    "invocation_completed", "invocation_failed", "notification_observed",
}
SENSITIVE_KEYS = {
    "authorization", "api_key", "apikey", "password", "passwd", "secret",
    "token", "access_token", "client_secret", "cookie", "set_cookie",
}


class AuditIntegrityError(RuntimeError):
    """Raised when raw audit events are not complete enough for quality analysis."""


@dataclass(frozen=True, slots=True)
class AuditInvocation:
    session_id: str
    sequence: int
    invocation_id: str
    previous_invocation_id: str | None
    client_request_id: Any = None


@dataclass(frozen=True, slots=True)
class _ActiveAudit:
    logger: "MCPAuditLogger"
    invocation: AuditInvocation | None
    parent_span_id: str | None = None


_ACTIVE_AUDIT: ContextVar[_ActiveAudit | None] = ContextVar(
    "project_knowledge_active_audit", default=None,
)
_PROCESS_APPEND_LOCK = threading.RLock()


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_path(parent: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    if key.replace("_", "").isalnum() and not key[:1].isdigit():
        return f"{parent}.{key}"
    return f"{parent}[{json.dumps(key, ensure_ascii=False)}]"


def redact_payload(value: Any) -> tuple[Any, list[dict[str, str]]]:
    """Recursively redact secrets while preserving all non-secret payload data."""
    scanner = SecretScanner()
    findings: list[dict[str, str]] = []

    def visit(item: Any, path: str, sensitive_key: str | None = None) -> Any:
        if sensitive_key is not None and item is not None and item != "":
            kind = sensitive_key.lower().replace("-", "_")
            findings.append({"path": path, "kind": kind})
            return f"[REDACTED:{kind}]"
        if isinstance(item, str):
            redacted, matches = scanner.redact(item)
            if matches:
                findings.append({
                    "path": path,
                    "kind": "+".join(sorted({match.kind for match in matches})),
                })
            return redacted
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in item.items():
                text_key = str(key)
                normalized = text_key.lower().replace("-", "_")
                child_path = _json_path(path, text_key)
                result[text_key] = visit(
                    child,
                    child_path,
                    text_key if normalized in SENSITIVE_KEYS else None,
                )
            return result
        if isinstance(item, (list, tuple)):
            return [visit(child, _json_path(path, index)) for index, child in enumerate(item)]
        return item

    return visit(value, "$"), findings


@contextmanager
def _append_lock(path: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lockdir")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    owner_token = uuid.uuid4().hex
    payload = json.dumps({
        "pid": os.getpid(), "created_at": time.time(), "token": owner_token,
    })
    while True:
        try:
            lock_path.mkdir()
            lock_path.joinpath("owner").write_text(payload, encoding="utf-8")
            break
        except FileExistsError:
            stale = False
            try:
                owner = json.loads(lock_path.joinpath("owner").read_text(encoding="utf-8"))
                stale = (
                    not process_alive(int(owner.get("pid", 0)))
                    or time.time() - float(owner.get("created_at", 0)) > 30
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                try:
                    stale = time.time() - lock_path.stat().st_mtime > 30
                except (FileNotFoundError, PermissionError):
                    continue
            if stale:
                try:
                    lock_path.joinpath("owner").unlink(missing_ok=True)
                    lock_path.rmdir()
                except (FileNotFoundError, OSError):
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out acquiring audit append lock: {lock_path}")
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            owner = json.loads(lock_path.joinpath("owner").read_text(encoding="utf-8"))
            if owner.get("token") == owner_token:
                lock_path.joinpath("owner").unlink(missing_ok=True)
                lock_path.rmdir()
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            pass


class MCPAuditLogger:
    def __init__(self, root: str | Path, *, protocol_version: str = "unknown") -> None:
        self.root = Path(root).resolve()
        self.log_path = self.root / ".project-kb" / "logs" / "mcp-events.jsonl"
        self.protocol_version = protocol_version
        self.session_id = _identifier("ses")
        self._sequence = 0
        self._previous_invocation_id: str | None = None
        self._message_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._closed = False
        self._thread_lock = threading.RLock()
        self._pending_gap: tuple[int, int, str] | None = None
        self._invocation_started_ns: dict[str, int] = {}
        self._project_id = "sha256:" + hashlib.sha256(
            str(self.root).encode("utf-8")
        ).hexdigest()
        self.emit("session_started", {"audit_policy": "local_full_payload_redacted"})

    def emit(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        invocation: AuditInvocation | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            safe_payload, redactions = redact_payload(payload)
            safe_client_request_id = None
            if invocation is not None:
                safe_client_request_id, client_id_redactions = redact_payload(
                    invocation.client_request_id,
                )
                redactions.extend({
                    "path": item["path"].replace("$", "$.client_request_id", 1),
                    "kind": item["kind"],
                } for item in client_id_redactions)
        except (TypeError, ValueError, RecursionError) as error:
            safe_payload = {
                "audit_redaction_error": type(error).__name__,
                "audit_redaction_message": str(error),
            }
            safe_client_request_id = None
            redactions = [{"path": "$.payload", "kind": "redaction_error"}]
        record = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "event_id": _identifier("evt"),
            "event": event,
            "timestamp": utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "project_id": self._project_id,
            "project_root": self.root.as_posix(),
            "server_version": __version__,
            "protocol_version": self.protocol_version,
            "pid": os.getpid(),
            "session_id": self.session_id,
            "sequence": invocation.sequence if invocation else self._sequence,
            "invocation_id": invocation.invocation_id if invocation else None,
            "previous_invocation_id": (
                invocation.previous_invocation_id if invocation else None
            ),
            "client_request_id": safe_client_request_id,
            "trace_id": invocation.invocation_id if invocation else None,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "payload": safe_payload,
            "redactions": redactions,
        }
        with self._thread_lock:
            try:
                if self._pending_gap is not None:
                    gap_start, gap_end, reason = self._pending_gap
                    gap = dict(record)
                    gap.update({
                        "event_id": _identifier("evt"),
                        "event": "audit_gap",
                        "invocation_id": None,
                        "trace_id": None,
                        "payload": {
                            "first_lost_sequence": gap_start,
                            "last_lost_sequence": gap_end,
                            "reason": reason,
                        },
                        "redactions": [],
                    })
                    self._write(gap)
                    self._pending_gap = None
                self._write(record)
            except (
                OSError, TimeoutError, TypeError, ValueError, RecursionError,
    ) as error:
                sequence = invocation.sequence if invocation else self._sequence
                if self._pending_gap is None:
                    self._pending_gap = (sequence, sequence, type(error).__name__)
                else:
                    self._pending_gap = (
                        self._pending_gap[0], sequence, self._pending_gap[2],
                    )
                print(f"project-kb audit write failed: {error}", file=sys.stderr)
        return record

    def _write(self, record: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ) + "\n"
        with _PROCESS_APPEND_LOCK:
            with _append_lock(self.log_path):
                with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())

    def begin_message(
        self,
        raw_line: str,
        *,
        request: dict[str, Any] | None = None,
        parse_error: str | None = None,
    ) -> AuditInvocation:
        self._sequence += 1
        self._message_count += 1
        invocation = AuditInvocation(
            session_id=self.session_id,
            sequence=self._sequence,
            invocation_id=_identifier("inv"),
            previous_invocation_id=self._previous_invocation_id,
            client_request_id=request.get("id") if request else None,
        )
        self._previous_invocation_id = invocation.invocation_id
        payload: dict[str, Any] = {"request": request}
        if request is None:
            payload["raw_line"] = raw_line.rstrip("\r\n")
        if parse_error:
            payload["parse_error"] = parse_error
        self.emit("message_received", payload, invocation=invocation)
        return invocation

    def start_invocation(
        self, invocation: AuditInvocation, request: dict[str, Any],
    ) -> None:
        params = request.get("params") or {}
        self.emit("invocation_started", {
            "method": request.get("method"),
            "tool": params.get("name") if isinstance(params, dict) else None,
            "request": request,
            "client_trace": params.get("_meta") if isinstance(params, dict) else None,
        }, invocation=invocation)
        self._invocation_started_ns[invocation.invocation_id] = time.monotonic_ns()

    def client_initialized(
        self, invocation: AuditInvocation, params: dict[str, Any], protocol: str,
    ) -> None:
        self.protocol_version = protocol
        self.emit("client_initialized", {
            "client_info": params.get("clientInfo"),
            "capabilities": params.get("capabilities"),
            "requested_protocol": params.get("protocolVersion"),
            "negotiated_protocol": protocol,
        }, invocation=invocation)

    def observe_notification(
        self, invocation: AuditInvocation, request: dict[str, Any],
    ) -> None:
        self.emit("notification_observed", {
            "method": request.get("method"),
            "params": request.get("params") or {},
            "response_reason": "json_rpc_notification",
        }, invocation=invocation)

    def complete_invocation(
        self,
        invocation: AuditInvocation,
        response: dict[str, Any],
        *,
        error: BaseException | str | None = None,
    ) -> None:
        serialized = json.dumps(
            response, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        result = response.get("result")
        failed = "error" in response or (
            isinstance(result, dict) and bool(result.get("isError"))
        ) or error is not None
        event = "invocation_failed" if failed else "invocation_completed"
        completed_ns = time.monotonic_ns()
        started_ns = self._invocation_started_ns.pop(invocation.invocation_id, completed_ns)
        payload: dict[str, Any] = {
            "response": response,
            "response_sha256": "sha256:" + hashlib.sha256(serialized).hexdigest(),
            "status": "error" if failed else "ok",
            "duration_ms": (completed_ns - started_ns) / 1_000_000,
        }
        if error is not None:
            payload["error_type"] = type(error).__name__ if not isinstance(error, str) else "error"
            payload["error_message"] = str(error)
        self.emit(event, payload, invocation=invocation)
        if failed:
            self._failure_count += 1
        else:
            self._success_count += 1

    @contextmanager
    def activate(self, invocation: AuditInvocation) -> Iterator[None]:
        token = _ACTIVE_AUDIT.set(_ActiveAudit(self, invocation))
        try:
            yield
        finally:
            _ACTIVE_AUDIT.reset(token)

    @contextmanager
    def activate_session(self) -> Iterator[None]:
        token = _ACTIVE_AUDIT.set(_ActiveAudit(self, None))
        try:
            yield
        finally:
            _ACTIVE_AUDIT.reset(token)

    def close(self) -> None:
        if self._closed:
            return
        self.emit("session_ended", {
            "last_sequence": self._sequence,
            "message_count": self._message_count,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "audit_health": "gap" if self._pending_gap else "ok",
        })
        self._closed = True


class AuditSpan:
    def __init__(self, kind: str, name: str, input_payload: Any) -> None:
        self.kind = kind
        self.name = name
        self.input_payload = input_payload
        self.output_payload: Any = None
        self.span_id = _identifier("spn")
        self._active: _ActiveAudit | None = None
        self._token: Token[_ActiveAudit | None] | None = None
        self._started_ns = 0

    def __enter__(self) -> "AuditSpan":
        self._active = _ACTIVE_AUDIT.get()
        self._started_ns = time.monotonic_ns()
        if self._active is not None:
            self._active.logger.emit("span_started", {
                "kind": self.kind,
                "name": self.name,
                "input": self.input_payload,
            }, invocation=self._active.invocation, span_id=self.span_id,
                parent_span_id=self._active.parent_span_id)
            self._token = _ACTIVE_AUDIT.set(_ActiveAudit(
                self._active.logger, self._active.invocation, self.span_id,
            ))
        return self

    def set_output(self, output: Any) -> Any:
        self.output_payload = output
        return output

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        if self._token is not None:
            _ACTIVE_AUDIT.reset(self._token)
        if self._active is None:
            return False
        duration_ms = (time.monotonic_ns() - self._started_ns) / 1_000_000
        if exc is None:
            self._active.logger.emit("span_completed", {
                "kind": self.kind,
                "name": self.name,
                "output": self.output_payload,
                "status": "ok",
                "duration_ms": duration_ms,
            }, invocation=self._active.invocation, span_id=self.span_id,
                parent_span_id=self._active.parent_span_id)
        else:
            failure_payload = {
                "kind": self.kind,
                "name": self.name,
                "error_type": exc_type.__name__ if exc_type else "Exception",
                "error_message": str(exc),
                "status": "error",
                "duration_ms": duration_ms,
            }
            if self.output_payload is not None:
                failure_payload["output"] = self.output_payload
            self._active.logger.emit("span_failed", failure_payload,
                invocation=self._active.invocation, span_id=self.span_id,
                parent_span_id=self._active.parent_span_id)
        return False


def audit_span(kind: str, name: str, input_payload: Any = None) -> AuditSpan:
    return AuditSpan(kind, name, input_payload)


def _read_events(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    if not path.exists():
        return [], [{"code": "no_audit_log", "path": str(path)}]
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            issues.append({
                "code": "invalid_json", "line": line_number, "message": error.msg,
            })
            continue
        if not isinstance(event, dict):
            issues.append({"code": "invalid_event", "line": line_number})
            continue
        try:
            validate_instance(event, AUDIT_EVENT_SCHEMA)
        except SchemaValidationError as error:
            issues.append({
                "code": "event_schema_invalid", "line": line_number,
                "message": str(error),
            })
        event["_line"] = line_number
        events.append(event)
    return events, issues


def validate_audit_log(path: str | Path) -> dict[str, Any]:
    log_path = Path(path)
    events, issues = _read_events(log_path)
    event_ids: set[str] = set()
    sessions: dict[str, list[dict[str, Any]]] = {}
    invocations: dict[str, list[dict[str, Any]]] = {}
    spans: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            issues.append({"code": "missing_event_id", "line": event.get("_line")})
        elif event_id in event_ids:
            issues.append({"code": "duplicate_event_id", "event_id": event_id})
        else:
            event_ids.add(event_id)
        session_id = event.get("session_id")
        if not session_id:
            issues.append({"code": "missing_session_id", "line": event.get("_line")})
            continue
        sessions.setdefault(str(session_id), []).append(event)
        invocation_id = event.get("invocation_id")
        if event.get("event") in {
            "message_received", "invocation_started", "invocation_completed",
            "invocation_failed", "notification_observed",
        } and not invocation_id:
            issues.append({
                "code": "missing_invocation_id", "event": event.get("event"),
                "line": event.get("_line"),
            })
        if event.get("event") == "span_started" and not invocation_id:
            kind = event.get("payload", {}).get("kind")
            if kind != "mcp.server":
                issues.append({
                    "code": "missing_span_invocation_id", "line": event.get("_line"),
                })
        if invocation_id:
            invocations.setdefault(str(invocation_id), []).append(event)
        span_id = event.get("span_id")
        if span_id:
            spans.setdefault(str(span_id), []).append(event)
        if event.get("event") == "audit_gap":
            issues.append({"code": "audit_gap", "line": event.get("_line")})

    for session_id, session_events in sessions.items():
        names = [event.get("event") for event in session_events]
        if names.count("session_started") != 1:
            issues.append({"code": "invalid_session_start", "session_id": session_id})
        if names.count("session_ended") != 1:
            issues.append({"code": "invalid_session_end", "session_id": session_id})
        sequences = sorted(
            int(event.get("sequence", 0))
            for event in session_events if event.get("event") == "message_received"
        )
        if sequences != list(range(1, len(sequences) + 1)):
            issues.append({
                "code": "sequence_gap", "session_id": session_id, "sequences": sequences,
            })
        ordered_messages = sorted(
            (
                event for event in session_events
                if event.get("event") == "message_received"
            ),
            key=lambda event: int(event.get("sequence", 0)),
        )
        previous_id: str | None = None
        for event in ordered_messages:
            if event.get("previous_invocation_id") != previous_id:
                issues.append({
                    "code": "invalid_invocation_chain",
                    "session_id": session_id,
                    "invocation_id": event.get("invocation_id"),
                })
            previous_id = event.get("invocation_id")

    for invocation_id, invocation_events in invocations.items():
        names = [event.get("event") for event in invocation_events]
        terminal_count = sum(name in TERMINAL_EVENTS for name in names)
        message_count_for_invocation = names.count("message_received")
        if message_count_for_invocation == 0:
            issues.append({"code": "missing_message", "invocation_id": invocation_id})
        elif message_count_for_invocation > 1:
            issues.append({"code": "duplicate_message", "invocation_id": invocation_id})
        if terminal_count == 0:
            issues.append({"code": "unclosed_invocation", "invocation_id": invocation_id})
        elif terminal_count > 1:
            issues.append({"code": "duplicate_terminal", "invocation_id": invocation_id})
        start_count = names.count("invocation_started")
        is_notification = "notification_observed" in names
        if not is_notification and start_count == 0:
            issues.append({
                "code": "missing_invocation_start", "invocation_id": invocation_id,
            })
        elif start_count > 1:
            issues.append({
                "code": "duplicate_invocation_start", "invocation_id": invocation_id,
            })
        elif is_notification and start_count:
            issues.append({
                "code": "unexpected_notification_start", "invocation_id": invocation_id,
            })
        session_ids = {event.get("session_id") for event in invocation_events}
        if len(session_ids) > 1:
            issues.append({
                "code": "cross_session_invocation", "invocation_id": invocation_id,
            })
        sequences_for_invocation = {
            event.get("sequence") for event in invocation_events
        }
        if len(sequences_for_invocation) > 1:
            issues.append({
                "code": "inconsistent_invocation_sequence",
                "invocation_id": invocation_id,
            })

    span_starts: dict[str, dict[str, Any]] = {}
    for span_id, span_events in spans.items():
        names = [event.get("event") for event in span_events]
        if names.count("span_started") != 1:
            issues.append({"code": "invalid_span_start", "span_id": span_id})
        if sum(name in {"span_completed", "span_failed"} for name in names) != 1:
            issues.append({"code": "unclosed_span", "span_id": span_id})
        start = next((event for event in span_events if event.get("event") == "span_started"), None)
        if start:
            span_starts[span_id] = start
        ownership = {
            (event.get("session_id"), event.get("invocation_id"))
            for event in span_events
        }
        if len(ownership) > 1:
            issues.append({"code": "inconsistent_span_ownership", "span_id": span_id})

    for span_id, start in span_starts.items():
        parent_id = start.get("parent_span_id")
        if not parent_id:
            continue
        parent = span_starts.get(str(parent_id))
        if parent is None:
            issues.append({"code": "orphan_span", "span_id": span_id})
            continue
        if parent.get("session_id") != start.get("session_id"):
            issues.append({
                "code": "cross_session_span_parent", "span_id": span_id,
            })
        if parent.get("invocation_id") != start.get("invocation_id"):
            issues.append({
                "code": "cross_invocation_span_parent", "span_id": span_id,
            })

    for span_id in span_starts:
        seen: set[str] = set()
        current: str | None = span_id
        while current:
            if current in seen:
                issues.append({"code": "span_cycle", "span_id": span_id})
                break
            seen.add(current)
            start = span_starts.get(current)
            current = str(start.get("parent_span_id")) if (
                start and start.get("parent_span_id")
            ) else None

    message_count = sum(event.get("event") == "message_received" for event in events)
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "path": str(log_path),
        "valid": not issues,
        "event_count": len(events),
        "session_count": len(sessions),
        "message_count": message_count,
        "invocation_count": sum(
            any(event.get("event") == "invocation_started" for event in group)
            for group in invocations.values()
        ),
        "span_count": len(spans),
        "issues": issues,
    }


def _extract_values(value: Any, keys: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                if isinstance(item, list):
                    found.extend(item)
                elif item is not None:
                    found.append(item)
            found.extend(_extract_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_extract_values(item, keys))
    return found


def _normalized_identifiers(values: list[Any], fields: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            candidate = value
        elif isinstance(value, dict):
            candidate = next((str(value[field]) for field in fields if value.get(field)), "")
        else:
            candidate = ""
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _normalized_call_paths(value: Any) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        return []
    if all(isinstance(item, str) for item in value):
        return [[str(item) for item in value]]
    paths: list[list[str]] = []
    for item in value:
        path: list[str] = []
        if isinstance(item, list) and all(isinstance(part, str) for part in item):
            path = [str(part) for part in item]
        elif isinstance(item, dict):
            for key in ("path", "nodes", "symbols"):
                candidate = item.get(key)
                if isinstance(candidate, list) and all(
                    isinstance(part, str) for part in candidate
                ):
                    path = [str(part) for part in candidate]
                    break
            if not path and isinstance(item.get("edges"), list):
                for edge in item["edges"]:
                    if not isinstance(edge, dict):
                        continue
                    source = edge.get("source")
                    target = edge.get("target")
                    if source and (not path or path[-1] != str(source)):
                        path.append(str(source))
                    if target:
                        path.append(str(target))
        if path and path not in paths:
            paths.append(path)
    return paths


def _prediction_values(value: Any, fields: tuple[str, ...]) -> list[str]:
    if not isinstance(value, list):
        value = [] if value is None else [value]
    return _normalized_identifiers(value, fields)


def observability_prediction(response: dict[str, Any], tool: str | None) -> dict[str, Any]:
    """Extract quality predictions only from documented result fields per MCP tool."""
    result = response.get("result") if isinstance(response, dict) else None
    structured = result.get("structuredContent", {}) if isinstance(result, dict) else {}
    if not isinstance(structured, dict):
        structured = {}

    files: Any = []
    symbols: Any = []
    knowledge: Any = []
    call_paths: Any = []
    extension_points: Any = []
    invariants: Any = []
    design_reasons: Any = []
    if tool == "knowledge_search":
        search_results = structured.get("results", [])
        files = [item.get("path") for item in search_results if isinstance(item, dict)]
        knowledge = [item.get("id") for item in search_results if isinstance(item, dict)]
    elif tool == "knowledge_get":
        files = [structured.get("path")]
        knowledge = [structured.get("id")]
    elif tool == "knowledge_impact":
        files = structured.get("affected_files", [])
        symbols = structured.get("affected_symbols", [])
        knowledge = structured.get("affected_knowledge", [])
        call_paths = structured.get("call_paths", [])
    elif tool == "knowledge_context":
        files = structured.get("files", [])
        symbols = structured.get("symbols", [])
        knowledge = structured.get("knowledge", [])
        impact = structured.get("impact", {})
        required = structured.get("required_evidence", {})
        call_paths = structured.get("call_paths", [])
        if not call_paths and isinstance(required, dict):
            call_paths = required.get("call_paths", required.get("relation_paths", []))
        if not call_paths and isinstance(impact, dict):
            call_paths = impact.get("call_path", [])
        extension_points = structured.get("extension_points", [])
        invariants = structured.get("invariants", [])
        design_reasons = structured.get("design_reasons", [])
    else:
        # Workflow tools may expose these analysis fields directly, but nested inputs
        # are intentionally ignored so request anchors cannot become predictions.
        files = structured.get("returned_files", structured.get("files", []))
        symbols = structured.get("returned_symbols", structured.get("symbols", []))
        knowledge = structured.get(
            "returned_knowledge_ids", structured.get("knowledge", []),
        )
        call_paths = structured.get("call_paths", [])
        extension_points = structured.get("extension_points", [])
        invariants = structured.get("invariants", [])
        design_reasons = structured.get("design_reasons", [])

    return {
        "returned_files": _prediction_values(files, ("path", "file")),
        "returned_symbols": _prediction_values(
            symbols, ("id", "symbol_id", "qualified_name", "name", "symbol"),
        ),
        "returned_knowledge_ids": _prediction_values(
            knowledge, ("id", "knowledge_id"),
        ),
        "call_paths": _normalized_call_paths(call_paths),
        "extension_points": _prediction_values(
            extension_points, ("symbol", "id", "name", "path"),
        ),
        "invariants": _prediction_values(
            invariants, ("id", "name", "text", "summary"),
        ),
        "design_reasons": _prediction_values(
            design_reasons, ("id", "name", "reason", "text", "summary"),
        ),
    }


def _without_internal(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "_line"}


def _has_client_correlation(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    explicit_keys = {
        "traceid", "traceparent", "taskid", "threadid", "conversationid",
    }
    return any(
        key.lower().replace("_", "").replace("-", "") in explicit_keys
        and value not in (None, "", [], {})
        for key, value in metadata.items()
    )


def export_audit_log(path: str | Path, output: str | Path) -> dict[str, Any]:
    log_path = Path(path)
    report = validate_audit_log(log_path)
    if not report["valid"]:
        codes = ", ".join(sorted({issue["code"] for issue in report["issues"]}))
        raise AuditIntegrityError(f"audit log is not complete: {codes}")
    events, _ = _read_events(log_path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        invocation_id = event.get("invocation_id")
        if invocation_id:
            grouped.setdefault(str(invocation_id), []).append(event)
    records: list[dict[str, Any]] = []
    for invocation_id, invocation_events in grouped.items():
        started = next(
            (event for event in invocation_events if event.get("event") == "invocation_started"),
            None,
        )
        terminal = next(
            (event for event in invocation_events if event.get("event") in {
                "invocation_completed", "invocation_failed",
            }),
            None,
        )
        if started is None or terminal is None:
            continue
        request = started.get("payload", {}).get("request", {})
        response = terminal.get("payload", {}).get("response", {})
        params = request.get("params", {}) if isinstance(request, dict) else {}
        spans_with_order: list[tuple[int, str, dict[str, Any]]] = []
        span_starts = {
            event.get("span_id"): event
            for event in invocation_events if event.get("event") == "span_started"
        }
        for event in invocation_events:
            if event.get("event") not in {"span_completed", "span_failed"}:
                continue
            start = span_starts.get(event.get("span_id"), {})
            span = {
                "span_id": event.get("span_id"),
                "parent_span_id": event.get("parent_span_id"),
                "kind": start.get("payload", {}).get("kind"),
                "name": start.get("payload", {}).get("name"),
                "input": start.get("payload", {}).get("input"),
                **event.get("payload", {}),
            }
            spans_with_order.append((
                int(start.get("monotonic_ns", 0)), str(event.get("span_id", "")), span,
            ))
        spans = [item[2] for item in sorted(spans_with_order)]
        record = {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "session_id": started.get("session_id"),
            "sequence": started.get("sequence"),
            "invocation_id": invocation_id,
            "previous_invocation_id": started.get("previous_invocation_id"),
            "client_request_id": started.get("client_request_id"),
            "protocol_version": terminal.get("protocol_version"),
            "server_version": terminal.get("server_version"),
            "method": request.get("method") if isinstance(request, dict) else None,
            "tool": params.get("name") if isinstance(params, dict) else None,
            "arguments": params.get("arguments", {}) if isinstance(params, dict) else {},
            "request": request,
            "response": response,
            "status": terminal.get("payload", {}).get("status"),
            "started_at": started.get("timestamp"),
            "completed_at": terminal.get("timestamp"),
            "duration_ms": terminal.get("payload", {}).get("duration_ms", 0.0),
            "error": (
                response.get("error") if isinstance(response, dict) else None
            ),
            "spans": spans,
            "prediction": observability_prediction(
                response, params.get("name") if isinstance(params, dict) else None,
            ),
            "ground_truth_ref": invocation_id,
            "causality": (
                "client_correlated" if _has_client_correlation(
                    started.get("payload", {}).get("client_trace")
                ) else "ordered_only"
            ),
            "integrity": "complete",
        }
        validate_instance(record, MCP_ANALYSIS_SCHEMA)
        records.append(record)
    records.sort(key=lambda item: (str(item["session_id"]), int(item["sequence"])))
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )
    atomic_write(Path(output), content)
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "source": str(log_path),
        "output": str(Path(output)),
        "record_count": len(records),
        "valid": True,
    }


def _read_jsonl_objects(path: Path, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} line {line_number}: invalid JSON: {error.msg}") from error
        if not isinstance(row, dict):
            raise ValueError(f"{label} line {line_number}: expected object")
        rows.append(row)
    return rows


def _path_predictions(value: Any) -> set[tuple[str, ...]]:
    if not isinstance(value, list) or not value:
        return set()
    if all(isinstance(item, str) for item in value):
        return {tuple(value)}
    return {
        tuple(str(part) for part in item)
        for item in value
        if isinstance(item, list) and item and all(isinstance(part, str) for part in item)
    }


def evaluate_audit_analysis(
    analysis_path: str | Path,
    ground_truth_path: str | Path,
) -> dict[str, Any]:
    """Evaluate exported predictions against independently maintained labels."""
    records = _read_jsonl_objects(Path(analysis_path), "analysis")
    labels = _read_jsonl_objects(Path(ground_truth_path), "ground truth")
    records_by_ref = {str(row.get("ground_truth_ref", "")): row for row in records}
    if len(records_by_ref) != len(records):
        raise ValueError("analysis contains duplicate or empty ground_truth_ref values")

    dimensions = ("file", "symbol", "call_path", "extension_point", "invariant", "design_reason")
    totals = {
        dimension: {
            "precision_matched": 0, "recall_matched": 0,
            "predicted": 0, "expected": 0,
        }
        for dimension in dimensions
    }
    per_invocation: list[dict[str, Any]] = []
    missing_refs: list[str] = []
    seen_refs: set[str] = set()
    for label in labels:
        reference = str(label.get("ground_truth_ref", ""))
        if not reference:
            raise ValueError("ground truth row is missing ground_truth_ref")
        for field in (
            "expected_files", "acceptable_supporting_files", "expected_symbols",
            "expected_extension_points", "expected_invariants", "expected_design_reasons",
        ):
            value = label.get(field, [])
            if not isinstance(value, list) or not all(isinstance(item, (str, int, float)) for item in value):
                raise ValueError(f"ground truth field {field} must be an array of scalar values")
        expected_path_value = label.get("expected_call_path", [])
        if not isinstance(expected_path_value, list) or not all(
            isinstance(item, str) for item in expected_path_value
        ):
            raise ValueError("ground truth field expected_call_path must be an array of strings")
        if reference in seen_refs:
            raise ValueError(f"duplicate ground_truth_ref in labels: {reference}")
        seen_refs.add(reference)
        record = records_by_ref.get(reference)
        if record is None:
            missing_refs.append(reference)
            continue
        prediction = record.get("prediction", {})
        row_metrics: dict[str, float] = {}
        for prefix, expected_key, prediction_key in (
            ("symbol", "expected_symbols", "returned_symbols"),
            ("extension_point", "expected_extension_points", "extension_points"),
            ("invariant", "expected_invariants", "invariants"),
            ("design_reason", "expected_design_reasons", "design_reasons"),
        ):
            expected = {str(item) for item in label.get(expected_key, [])}
            predicted = {str(item) for item in prediction.get(prediction_key, [])}
            matched = len(expected & predicted)
            totals[prefix]["precision_matched"] += matched
            totals[prefix]["recall_matched"] += matched
            totals[prefix]["predicted"] += len(predicted)
            totals[prefix]["expected"] += len(expected)
            if predicted or expected:
                row_metrics[f"{prefix}_precision"] = matched / max(1, len(predicted))
                row_metrics[f"{prefix}_recall"] = matched / max(1, len(expected))

        expected_files = {str(item) for item in label.get("expected_files", [])}
        acceptable_files = {
            str(item) for item in label.get("acceptable_supporting_files", [])
        }
        predicted_files = {
            str(item) for item in prediction.get("returned_files", [])
        }
        precision_matched_files = len(predicted_files & (expected_files | acceptable_files))
        recall_matched_files = len(predicted_files & expected_files)
        totals["file"]["precision_matched"] += precision_matched_files
        totals["file"]["recall_matched"] += recall_matched_files
        totals["file"]["predicted"] += len(predicted_files)
        totals["file"]["expected"] += len(expected_files)
        if predicted_files or expected_files:
            row_metrics["file_precision"] = precision_matched_files / max(1, len(predicted_files))
            row_metrics["file_recall"] = recall_matched_files / max(1, len(expected_files))

        expected_path = label.get("expected_call_path", [])
        expected_paths = {tuple(str(item) for item in expected_path)} if expected_path else set()
        predicted_paths = _path_predictions(prediction.get("call_paths", []))
        matched_paths = len(expected_paths & predicted_paths)
        totals["call_path"]["precision_matched"] += matched_paths
        totals["call_path"]["recall_matched"] += matched_paths
        totals["call_path"]["predicted"] += len(predicted_paths)
        totals["call_path"]["expected"] += len(expected_paths)
        if predicted_paths or expected_paths:
            row_metrics["call_path_precision"] = matched_paths / max(1, len(predicted_paths))
            row_metrics["call_path_recall"] = matched_paths / max(1, len(expected_paths))
        per_invocation.append({
            "ground_truth_ref": reference,
            "invocation_id": record.get("invocation_id"),
            "tool": record.get("tool"),
            "metrics": row_metrics,
        })

    if missing_refs:
        raise ValueError("ground truth references missing analysis rows: " + ", ".join(missing_refs))
    metrics: dict[str, float] = {}
    not_applicable: list[str] = []
    for prefix, counts in totals.items():
        if counts["predicted"] or counts["expected"]:
            metrics[f"{prefix}_precision"] = counts["precision_matched"] / max(1, counts["predicted"])
            metrics[f"{prefix}_recall"] = counts["recall_matched"] / max(1, counts["expected"])
        else:
            not_applicable.append(prefix)
    return {
        "schema_version": 1,
        "analysis": str(Path(analysis_path)),
        "ground_truth": str(Path(ground_truth_path)),
        "record_count": len(records),
        "evaluated_count": len(per_invocation),
        "label_coverage": len(per_invocation) / max(1, len(records)),
        "metrics": metrics,
        "not_applicable_dimensions": not_applicable,
        "counts": totals,
        "per_invocation": per_invocation,
    }
