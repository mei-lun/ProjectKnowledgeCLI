from __future__ import annotations

import unittest

from project_knowledge.models import KnowledgeRecord
from project_knowledge.schemas import (
    EVIDENCE_PACK_SCHEMA,
    KNOWLEDGE_RECORD_SCHEMA,
    PROPOSAL_SCHEMA,
    SchemaValidationError,
    validate_instance,
)


class SchemaTests(unittest.TestCase):
    def test_required_empty_collections_are_preserved_and_validate(self) -> None:
        payload = KnowledgeRecord(
            id="generated.routes",
            kind="route",
            title="路由",
            path=".project-kb/generated/routes.md",
            ownership="generated",
            confidence="generated",
        ).to_dict()
        self.assertEqual(payload["sources"], [])
        validate_instance(payload, KNOWLEDGE_RECORD_SCHEMA)

    def test_runtime_validator_rejects_invalid_record(self) -> None:
        invalid = {
            "id": "",
            "kind": "route",
            "title": "路由",
            "path": ".project-kb/generated/routes.md",
            "ownership": "automatic",
            "confidence": "generated",
            "status": "fresh",
            "sources": [],
        }
        with self.assertRaises(SchemaValidationError):
            validate_instance(invalid, KNOWLEDGE_RECORD_SCHEMA)

    def test_evidence_pack_schema_rejects_absolute_paths_and_unhashed_payloads(self) -> None:
        invalid = {
            "schema_version": 1,
            "task": "新增功能",
            "items": [{
                "kind": "file", "path": "/tmp/secret.py", "content": "x",
                "content_hash": "bad", "tokens": 1, "redactions": [],
            }],
            "omitted": [],
            "files_considered": 1,
            "files_included": 1,
            "estimated_tokens": 1,
            "pack_hash": "bad",
        }
        with self.assertRaises(SchemaValidationError):
            validate_instance(invalid, EVIDENCE_PACK_SCHEMA)

    def test_proposal_schema_requires_structured_patch_operations(self) -> None:
        invalid = {
            "schema_version": 1, "proposal_id": "kp-0123456789abcdef",
            "target": ".project-kb/curated/architecture.md", "target_hash": "sha256:" + "0" * 64,
            "reason": "更新入口", "evidence": ["src/app.py"], "confidence": 0.8,
            "source_hashes": {},
            "operations": ["update_source_reference"], "requires_review": True,
            "status": "pending", "created_at": "2026-08-07T00:00:00+08:00",
        }
        with self.assertRaises(SchemaValidationError):
            validate_instance(invalid, PROPOSAL_SCHEMA)


if __name__ == "__main__":
    unittest.main()
