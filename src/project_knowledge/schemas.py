from __future__ import annotations

import re
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when a runtime payload violates its published JSON Schema subset."""


def validate_instance(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the JSON Schema features used by Project Knowledge System payloads."""
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: {value!r} 不在允许值 {schema['enum']!r} 中")

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, choice) for choice in choices):
            raise SchemaValidationError(f"{path}: 期望类型 {choices!r}，实际为 {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaValidationError(f"{path}: 缺少必填字段 {key!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_instance(item, properties[key], child_path)
            elif additional is False:
                raise SchemaValidationError(f"{path}: 不允许字段 {key!r}")
            elif isinstance(additional, dict):
                validate_instance(item, additional, child_path)

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise SchemaValidationError(f"{path}: 数组元素少于 {schema['minItems']} 个")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: 数组元素多于 {schema['maxItems']} 个")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                validate_instance(item, schema["items"], f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaValidationError(f"{path}: 字符串长度小于 {schema['minLength']}")
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, value) is None:
            raise SchemaValidationError(f"{path}: 不匹配模式 {pattern!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaValidationError(f"{path}: 小于最小值 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaValidationError(f"{path}: 大于最大值 {schema['maximum']}")


def _matches_type(value: Any, expected: str) -> bool:
    matches = {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }
    return matches.get(expected, False)


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
        "ownership": {"enum": ["generated", "draft", "curated", "decision"]},
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

RELATIVE_PATH_PATTERN = r"^(?!/)(?![A-Za-z]:[\\/])(?!.*(?:^|/)\.\.(?:/|$)).+"

PATCH_OPERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["op"],
    "properties": {
        "op": {"enum": ["upsert_generated_block", "delete_generated_block", "append_adr_draft"]},
        "content": {"type": "string"},
        "block_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{0,63}$"},
        "supersedes": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "deleted_sources": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
    },
    "additionalProperties": False,
}

PROPOSAL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/proposal-v1.json",
    "type": "object",
    "required": [
        "schema_version", "proposal_id", "target", "target_hash", "reason", "evidence", "source_hashes",
        "confidence", "operations", "created_at", "requires_review", "status",
    ],
    "properties": {
        "schema_version": {"enum": [1]},
        "proposal_id": {"type": "string", "pattern": "^kp-[0-9a-f]{16}$"},
        "target": {"type": "string", "pattern": RELATIVE_PATH_PATTERN},
        "target_hash": {"type": "string", "pattern": "^(?:sha256:[0-9a-f]{64}|missing)$"},
        "reason": {"type": "string", "minLength": 1},
        "evidence": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "source_hashes": {
            "type": "object",
            "additionalProperties": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "operations": {"type": "array", "minItems": 1, "items": PATCH_OPERATION_SCHEMA},
        "created_at": {"type": "string", "minLength": 1},
        "change_range": {"type": "string", "minLength": 1},
        "requires_review": {"type": "boolean"},
        "status": {"enum": ["pending", "applied", "rejected", "conflicted"]},
        "reviewer": {"type": "string", "minLength": 1},
        "reviewed_at": {"type": "string", "minLength": 1},
        "review_reason": {"type": "string", "minLength": 1},
        "result_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "conflict_reason": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}

SECRET_REDACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["kind", "line", "replacement"],
    "properties": {
        "kind": {"type": "string", "minLength": 1},
        "line": {"type": "integer", "minimum": 1},
        "replacement": {"type": "string", "pattern": r"^\[REDACTED:[a-z0-9_-]+\]$"},
    },
    "additionalProperties": False,
}

OMITTED_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["path", "reason"],
    "properties": {
        "path": {"type": "string", "pattern": RELATIVE_PATH_PATTERN},
        "reason": {"enum": ["high_risk_path", "file_limit", "token_limit", "unreadable"]},
    },
    "additionalProperties": False,
}

EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["kind", "path", "content", "content_hash", "tokens", "redactions"],
    "properties": {
        "kind": {"enum": ["file", "symbol", "knowledge", "config", "relation"]},
        "path": {"type": "string", "pattern": RELATIVE_PATH_PATTERN},
        "content": {"type": "string"},
        "content_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "tokens": {"type": "integer", "minimum": 0},
        "redactions": {"type": "array", "items": SECRET_REDACTION_SCHEMA},
        "symbol_id": {"type": "string", "minLength": 1},
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}

EVIDENCE_PACK_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/evidence-pack-v1.json",
    "type": "object",
    "required": [
        "schema_version", "task", "items", "omitted", "files_considered",
        "files_included", "estimated_tokens", "pack_hash",
    ],
    "properties": {
        "schema_version": {"enum": [1]},
        "task": {"type": "string", "minLength": 1},
        "items": {"type": "array", "items": EVIDENCE_ITEM_SCHEMA},
        "omitted": {"type": "array", "items": OMITTED_EVIDENCE_SCHEMA},
        "files_considered": {"type": "integer", "minimum": 0},
        "files_included": {"type": "integer", "minimum": 0},
        "estimated_tokens": {"type": "integer", "minimum": 0},
        "pack_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "source_commit": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}


SEMANTIC_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type", "path", "line", "hash", "authority"],
    "properties": {
        "type": {"enum": ["file", "symbol"]},
        "path": {"type": "string", "pattern": RELATIVE_PATH_PATTERN},
        "id": {"type": "string", "minLength": 1},
        "line": {"type": "integer", "minimum": 1},
        "hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "authority": {"enum": ["source", "candidate"]},
    },
    "additionalProperties": False,
}

FEATURE_STATEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["text", "sources"],
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "sources": {"type": "array", "minItems": 1, "items": SEMANTIC_SOURCE_SCHEMA},
    },
    "additionalProperties": False,
}

WORKFLOW_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["order", "text", "sources"],
    "properties": {
        "order": {"type": "integer", "minimum": 1},
        "text": {"type": "string", "minLength": 1},
        "sources": {"type": "array", "minItems": 1, "items": SEMANTIC_SOURCE_SCHEMA},
    },
    "additionalProperties": False,
}

WORKFLOW_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/workflow-v1.json",
    "type": "object",
    "required": ["title", "steps"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "steps": {"type": "array", "minItems": 1, "items": WORKFLOW_STEP_SCHEMA},
    },
    "additionalProperties": False,
}

RECIPE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/recipe-v1.json",
    "type": "object",
    "required": ["title", "goal", "prerequisites", "steps", "verification", "rollback"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "goal": {"type": "string", "minLength": 1},
        "prerequisites": {"type": "array", "items": FEATURE_STATEMENT_SCHEMA},
        "steps": {"type": "array", "minItems": 1, "items": FEATURE_STATEMENT_SCHEMA},
        "verification": {"type": "array", "minItems": 1, "items": FEATURE_STATEMENT_SCHEMA},
        "rollback": {"type": "array", "items": FEATURE_STATEMENT_SCHEMA},
    },
    "additionalProperties": False,
}

UNKNOWN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["text", "reason", "needed_evidence"],
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "reason": {"type": "string", "minLength": 1},
        "needed_evidence": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "additionalProperties": False,
}

FEATURE_GUIDE_DRAFT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/feature-guide-draft-v1.json",
    "type": "object",
    "required": [
        "schema_version", "feature_id", "title", "domain", "lifecycle", "summary",
        "responsibilities", "entrypoints", "workflow", "dependencies", "data_and_state",
        "invariants", "extension_points", "recipe", "tests", "pitfalls", "unknowns",
    ],
    "properties": {
        "schema_version": {"enum": [1]},
        "feature_id": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
        "title": {"type": "string", "minLength": 1},
        "domain": {"type": "string", "minLength": 1},
        "lifecycle": {"enum": ["draft"]},
        "summary": FEATURE_STATEMENT_SCHEMA,
        "responsibilities": {"type": "array", "minItems": 1, "items": FEATURE_STATEMENT_SCHEMA},
        "entrypoints": {"type": "array", "minItems": 1, "items": FEATURE_STATEMENT_SCHEMA},
        "workflow": WORKFLOW_SCHEMA,
        "dependencies": {"type": "array", "items": FEATURE_STATEMENT_SCHEMA},
        "data_and_state": {"type": "array", "items": FEATURE_STATEMENT_SCHEMA},
        "invariants": {"type": "array", "items": FEATURE_STATEMENT_SCHEMA},
        "extension_points": {"type": "array", "minItems": 1, "items": FEATURE_STATEMENT_SCHEMA},
        "recipe": RECIPE_SCHEMA,
        "tests": {"type": "array", "minItems": 1, "items": FEATURE_STATEMENT_SCHEMA},
        "pitfalls": {"type": "array", "items": FEATURE_STATEMENT_SCHEMA},
        "unknowns": {"type": "array", "items": UNKNOWN_SCHEMA},
    },
    "additionalProperties": False,
}


RUN_STATUS_VALUES = [
    "scanning", "category_review", "categories_confirmed",
    "guidance_generation", "guidance_review", "complete", "failed",
]
BATCH_STATUS_VALUES = ["pending", "completed", "failed"]
DRAFT_KIND_VALUES = ["category_catalog", "guidance"]
DRAFT_STATUS_VALUES = [
    "incomplete", "awaiting_confirmation", "confirmed", "rejected",
]
UPDATE_LEVEL_VALUES = ["fact", "guidance", "category"]
ISO_TIME_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
}

GUIDANCE_RUN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/guidance-run-v2.json",
    "type": "object",
    "required": [
        "run_id", "project_root", "snapshot_id", "status", "total_files",
        "covered_files", "created_at", "updated_at", "uncovered_files", "error",
    ],
    "properties": {
        "run_id": {"type": "string", "minLength": 1},
        "project_root": {"type": "string", "minLength": 1},
        "snapshot_id": {"type": "string", "minLength": 1},
        "status": {"enum": RUN_STATUS_VALUES},
        "total_files": {"type": "integer", "minimum": 0},
        "covered_files": {"type": "integer", "minimum": 0},
        "uncovered_files": {"type": "array", "items": {"type": "string"}},
        "error": {"type": ["string", "null"]},
        "created_at": ISO_TIME_SCHEMA,
        "updated_at": ISO_TIME_SCHEMA,
    },
    "additionalProperties": False,
}

GUIDANCE_BATCH_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/guidance-batch-v2.json",
    "type": "object",
    "required": [
        "batch_id", "run_id", "ordinal", "status", "files", "snapshot_id",
        "created_at", "updated_at", "result", "error",
    ],
    "properties": {
        "batch_id": {"type": "string", "minLength": 1},
        "run_id": {"type": "string", "minLength": 1},
        "ordinal": {"type": "integer", "minimum": 0},
        "status": {"enum": BATCH_STATUS_VALUES},
        "files": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "snapshot_id": {"type": "string", "minLength": 1},
        "result": {"type": ["object", "null"]},
        "error": {"type": ["string", "null"]},
        "created_at": ISO_TIME_SCHEMA,
        "updated_at": ISO_TIME_SCHEMA,
    },
    "additionalProperties": False,
}

GUIDANCE_CATEGORY_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/guidance-category-v2.json",
    "type": "object",
    "required": [
        "category_id", "run_id", "name", "purpose", "applies_to", "excludes",
        "samples", "evidence", "confidence", "unknowns", "created_at",
        "updated_at", "relations",
    ],
    "properties": {
        "category_id": {"type": "string", "minLength": 1},
        "run_id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "purpose": {"type": "string"},
        "applies_to": {"type": "array", "items": {"type": "string"}},
        "excludes": {"type": "array", "items": {"type": "string"}},
        "samples": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "unknowns": {"type": "array"},
        "relations": {"type": "array", "items": {"type": "object"}},
        "created_at": ISO_TIME_SCHEMA,
        "updated_at": ISO_TIME_SCHEMA,
    },
    "additionalProperties": False,
}

GUIDANCE_DRAFT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/guidance-draft-v2.json",
    "type": "object",
    "required": [
        "draft_id", "run_id", "kind", "status", "path", "content_hash",
        "snapshot_id", "payload", "created_at", "updated_at", "category_id",
        "rejection_reason", "confirmed_at",
    ],
    "properties": {
        "draft_id": {"type": "string", "minLength": 1},
        "run_id": {"type": "string", "minLength": 1},
        "kind": {"enum": DRAFT_KIND_VALUES},
        "status": {"enum": DRAFT_STATUS_VALUES},
        "path": {"type": "string", "minLength": 1},
        "content_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "snapshot_id": {"type": "string", "minLength": 1},
        "payload": {"type": "object"},
        "category_id": {"type": ["string", "null"]},
        "rejection_reason": {"type": ["string", "null"]},
        "confirmed_at": {"type": ["string", "null"]},
        "created_at": ISO_TIME_SCHEMA,
        "updated_at": ISO_TIME_SCHEMA,
    },
    "additionalProperties": False,
}

GUIDANCE_VERSION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/guidance-version-v2.json",
    "type": "object",
    "required": [
        "version_id", "category_id", "version", "title", "content",
        "content_hash", "snapshot_id", "evidence", "is_current", "created_at",
        "draft_id",
    ],
    "properties": {
        "version_id": {"type": "string", "minLength": 1},
        "category_id": {"type": "string", "minLength": 1},
        "version": {"type": "integer", "minimum": 1},
        "title": {"type": "string", "minLength": 1},
        "content": {"type": "string"},
        "content_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "snapshot_id": {"type": "string", "minLength": 1},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "is_current": {"type": "boolean"},
        "created_at": ISO_TIME_SCHEMA,
        "draft_id": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

GUIDANCE_CHANGE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/guidance-change-v2.json",
    "type": "object",
    "required": [
        "change_id", "base_snapshot_id", "head_snapshot_id", "update_level",
        "changed_files", "affected_categories", "payload", "created_at", "processed_at",
    ],
    "properties": {
        "change_id": {"type": "string", "minLength": 1},
        "base_snapshot_id": {"type": "string", "minLength": 1},
        "head_snapshot_id": {"type": "string", "minLength": 1},
        "update_level": {"enum": UPDATE_LEVEL_VALUES},
        "changed_files": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "affected_categories": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "payload": {"type": "object"},
        "created_at": ISO_TIME_SCHEMA,
        "processed_at": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}


CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://project-kb.local/schema/config-v1.json",
    "type": "object",
    "required": ["version"],
    "properties": {
        "version": {"enum": [0, 1]},
        "project": {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
            "additionalProperties": True,
        },
        "index": {
            "type": "object",
            "properties": {
                "engine": {"enum": ["builtin", "codegraph"]},
                "codegraph_command": {"type": "string"},
                "codegraph_dir": {"type": "string", "pattern": "^[A-Za-z0-9._-]+$"},
                "codegraph_timeout_seconds": {"type": "integer", "minimum": 1},
                "include": {"type": "array", "items": {"type": "string"}},
                "exclude": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
        "knowledge": {"type": "object", "additionalProperties": True},
        "updates": {"type": "object", "additionalProperties": True},
        "retrieval": {"type": "object", "additionalProperties": True},
        "privacy": {"type": "object", "additionalProperties": True},
        "provider": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": True,
}


def all_schemas() -> dict[str, dict[str, Any]]:
    return {
        "config-v1.json": CONFIG_SCHEMA,
        "source-reference-v1.json": SOURCE_REFERENCE_SCHEMA,
        "knowledge-record-v1.json": KNOWLEDGE_RECORD_SCHEMA,
        "change-set-v1.json": CHANGE_SET_SCHEMA,
        "proposal-v1.json": PROPOSAL_SCHEMA,
        "evidence-pack-v1.json": EVIDENCE_PACK_SCHEMA,
        "workflow-v1.json": WORKFLOW_SCHEMA,
        "recipe-v1.json": RECIPE_SCHEMA,
        "feature-guide-draft-v1.json": FEATURE_GUIDE_DRAFT_SCHEMA,
        "guidance-run-v2.json": GUIDANCE_RUN_SCHEMA,
        "guidance-batch-v2.json": GUIDANCE_BATCH_SCHEMA,
        "guidance-category-v2.json": GUIDANCE_CATEGORY_SCHEMA,
        "guidance-draft-v2.json": GUIDANCE_DRAFT_SCHEMA,
        "guidance-version-v2.json": GUIDANCE_VERSION_SCHEMA,
        "guidance-change-v2.json": GUIDANCE_CHANGE_SCHEMA,
    }
