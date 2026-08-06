from __future__ import annotations

from typing import Any


SOURCE_REFERENCE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/source-reference-v1.json",
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {"enum": ["file", "symbol", "commit", "config", "task", "decision"]},
        "path": {"type": "string"},
        "id": {"type": "string"},
        "line": {"type": "integer", "minimum": 1},
        "hash": {"type": "string", "pattern": "^sha256:"},
    },
    "additionalProperties": False,
}

KNOWLEDGE_RECORD_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/knowledge-record-v1.json",
    "type": "object",
    "required": ["id", "kind", "title", "path", "ownership", "confidence", "status", "sources"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "path": {"type": "string", "minLength": 1},
        "ownership": {"enum": ["generated", "curated", "decision"]},
        "confidence": {"enum": ["verified", "generated", "inferred"]},
        "status": {"enum": ["fresh", "potentially_stale", "stale", "conflicted"]},
        "sources": {"type": "array", "items": SOURCE_REFERENCE_SCHEMA},
        "source_commit": {"type": ["string", "null"]},
        "source_hashes": {"type": "object", "additionalProperties": {"type": "string"}},
        "last_generated_at": {"type": ["string", "null"]},
        "last_verified_at": {"type": ["string", "null"]},
        "supersedes": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

CHANGE_SET_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/change-set-v1.json",
    "type": "object",
    "required": ["id", "task_summary", "changed_files", "changed_symbols", "affected_modules", "affected_knowledge", "tests_run", "test_results", "author"],
    "properties": {
        "id": {"type": "string"},
        "base_commit": {"type": ["string", "null"]},
        "head_commit": {"type": ["string", "null"]},
        "task_summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "changed_symbols": {"type": "array", "items": {"type": "string"}},
        "affected_modules": {"type": "array", "items": {"type": "string"}},
        "affected_knowledge": {"type": "array", "items": {"type": "string"}},
        "tests_run": {"type": "array", "items": {"type": "string"}},
        "test_results": {"type": "array", "items": {"type": "string"}},
        "author": {"type": "string"},
    },
    "additionalProperties": False,
}

PROPOSAL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/proposal-v1.json",
    "type": "object",
    "required": ["proposal_id", "target", "reason", "evidence", "confidence", "operations", "requires_review", "status"],
    "properties": {
        "proposal_id": {"type": "string"},
        "target": {"type": "string"},
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "operations": {"type": "array", "items": {"type": "string"}},
        "requires_review": {"type": "boolean"},
        "status": {"enum": ["pending", "applied", "rejected"]},
    },
    "additionalProperties": False,
}


def all_schemas() -> dict[str, dict[str, Any]]:
    return {
        "source-reference-v1.json": SOURCE_REFERENCE_SCHEMA,
        "knowledge-record-v1.json": KNOWLEDGE_RECORD_SCHEMA,
        "change-set-v1.json": CHANGE_SET_SCHEMA,
        "proposal-v1.json": PROPOSAL_SCHEMA,
    }
