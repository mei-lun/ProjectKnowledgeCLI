from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from project_knowledge.guidance_models import (
    GuidanceBatch,
    GuidanceCategory,
    GuidanceChange,
    GuidanceDraft,
    GuidanceRun,
    GuidanceVersion,
)
from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.models import KnowledgeRecord
from project_knowledge.schemas import (
    GUIDANCE_BATCH_SCHEMA,
    GUIDANCE_CATEGORY_SCHEMA,
    GUIDANCE_CHANGE_SCHEMA,
    GUIDANCE_DRAFT_SCHEMA,
    GUIDANCE_RUN_SCHEMA,
    GUIDANCE_VERSION_SCHEMA,
    SchemaValidationError,
    validate_instance,
)
from project_knowledge.store import KnowledgeStore


NOW = "2026-08-12T10:00:00+08:00"


class GuidanceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "index.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_initialize_migrates_v1_without_losing_knowledge(self) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata(key, value) VALUES ('schema_version', '1');
            CREATE TABLE knowledge (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                ownership TEXT NOT NULL,
                confidence TEXT NOT NULL,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                source_commit TEXT,
                source_hashes_json TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                supersedes_json TEXT NOT NULL,
                last_generated_at TEXT,
                last_verified_at TEXT
            );
            INSERT INTO knowledge(
                id, kind, title, path, ownership, confidence, status, content,
                source_commit, source_hashes_json, sources_json, tags_json,
                supersedes_json, last_generated_at, last_verified_at
            ) VALUES (
                'existing.knowledge', 'module', '已有知识', '.project-kb/existing.md',
                'curated', 'verified', 'fresh', '保留内容', NULL, '{}', '[]', '[]',
                '[]', NULL, NULL
            );
            """
        )
        connection.close()

        with KnowledgeStore(self.db_path) as store:
            store.initialize()
            self.assertEqual(store.get_meta("schema_version"), "2")
            self.assertEqual(store.get_knowledge("existing.knowledge"), self._knowledge())
            tables = {
                row[0]
                for row in store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue(
                {
                    "guidance_runs",
                    "guidance_batches",
                    "guidance_categories",
                    "guidance_drafts",
                    "guidance_versions",
                    "guidance_changes",
                }.issubset(tables)
            )

            GuidanceStore(store).create_run(self._run())
            store.initialize()
            self.assertEqual(store.get_knowledge("existing.knowledge"), self._knowledge())
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM guidance_runs WHERE run_id = 'run-stable'"
                ).fetchone()[0],
                1,
            )

    def test_models_round_trip_and_reject_invalid_statuses(self) -> None:
        models_and_schemas = [
            (self._run(), GUIDANCE_RUN_SCHEMA),
            (self._batch(), GUIDANCE_BATCH_SCHEMA),
            (self._category(), GUIDANCE_CATEGORY_SCHEMA),
            (self._draft(), GUIDANCE_DRAFT_SCHEMA),
            (self._version("version-1", 1, True), GUIDANCE_VERSION_SCHEMA),
            (self._change(), GUIDANCE_CHANGE_SCHEMA),
        ]
        for model, schema in models_and_schemas:
            with self.subTest(model=type(model).__name__):
                self.assertEqual(type(model).from_dict(model.to_dict()), model)
                validate_instance(model.to_dict(), schema)

        with self.assertRaises(ValueError):
            GuidanceRun.from_dict({**self._run().to_dict(), "status": "unknown"})
        with self.assertRaises(ValueError):
            GuidanceBatch.from_dict({**self._batch().to_dict(), "status": "unknown"})
        with self.assertRaises(ValueError):
            GuidanceDraft.from_dict({**self._draft().to_dict(), "kind": "unknown"})
        with self.assertRaises(ValueError):
            GuidanceChange.from_dict({**self._change().to_dict(), "update_level": "unknown"})
        with self.assertRaises(SchemaValidationError):
            validate_instance(
                {**self._draft().to_dict(), "status": "unknown"},
                GUIDANCE_DRAFT_SCHEMA,
            )

    def test_version_requires_real_boolean_current_flag(self) -> None:
        payload = {**self._version("version-1", 1, True).to_dict(), "is_current": "false"}
        with self.assertRaises(ValueError):
            GuidanceVersion(**payload)
        with self.assertRaises(ValueError):
            GuidanceVersion.from_dict(payload)

    def test_optional_foreign_ids_are_none_or_non_empty(self) -> None:
        draft_payload = {**self._draft().to_dict(), "category_id": ""}
        version_payload = {
            **self._version("version-1", 1, True).to_dict(),
            "draft_id": "",
        }
        with self.assertRaises(ValueError):
            GuidanceDraft.from_dict(draft_payload)
        with self.assertRaises(ValueError):
            GuidanceVersion.from_dict(version_payload)
        with self.assertRaises(SchemaValidationError):
            validate_instance(draft_payload, GUIDANCE_DRAFT_SCHEMA)
        with self.assertRaises(SchemaValidationError):
            validate_instance(version_payload, GUIDANCE_VERSION_SCHEMA)

    def test_optional_review_times_must_be_iso_8601(self) -> None:
        draft_payload = {**self._draft().to_dict(), "confirmed_at": "not-a-time"}
        change_payload = {**self._change().to_dict(), "processed_at": "not-a-time"}
        with self.assertRaises(ValueError):
            GuidanceDraft.from_dict(draft_payload)
        with self.assertRaises(ValueError):
            GuidanceChange.from_dict(change_payload)
        with self.assertRaises(SchemaValidationError):
            validate_instance(draft_payload, GUIDANCE_DRAFT_SCHEMA)
        with self.assertRaises(SchemaValidationError):
            validate_instance(change_payload, GUIDANCE_CHANGE_SCHEMA)

    def test_batch_upsert_is_idempotent(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.save_batch(self._batch())
            guidance.save_batch(
                GuidanceBatch(
                    **{
                        **self._batch().to_dict(),
                        "status": "completed",
                        "result": {"categories": ["activity"]},
                        "updated_at": "2026-08-12T10:05:00+08:00",
                    }
                )
            )
            batches = guidance.list_batches("run-stable")
            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0].status, "completed")
            self.assertEqual(batches[0].result, {"categories": ["activity"]})
            self.assertIsNone(guidance.next_pending_batch("run-stable"))

    def test_batch_id_cannot_move_to_another_run_or_ordinal(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.save_batch(self._batch())
            with self.assertRaises(ValueError):
                guidance.save_batch(
                    GuidanceBatch(
                        **{
                            **self._batch().to_dict(),
                            "run_id": "another-run",
                            "ordinal": 1,
                        }
                    )
                )

    def test_existing_version_is_immutable(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.save_category(self._category())
            guidance.save_version(self._version("version-1", 1, True))
            changed = GuidanceVersion(
                **{**self._version("version-1", 1, True).to_dict(), "content": "changed"}
            )
            with self.assertRaises(ValueError):
                guidance.save_version(changed)
            self.assertEqual(
                guidance.current_version("category-stable").content,
                "正文",
            )

    def test_category_rename_keeps_category_id(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.save_category(self._category())
            guidance.save_category(
                GuidanceCategory(
                    **{**self._category().to_dict(), "name": "通用运营活动"}
                )
            )
            categories = guidance.list_categories("run-stable")
            self.assertEqual(len(categories), 1)
            self.assertEqual(categories[0].category_id, "category-stable")
            self.assertEqual(categories[0].name, "通用运营活动")

    def test_only_one_current_version_per_category(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.save_category(self._category())
            guidance.save_version(self._version("version-1", 1, True))
            guidance.save_version(self._version("version-2", 2, True))
            self.assertEqual(
                guidance.current_version("category-stable").version_id,
                "version-2",
            )
            versions = guidance.list_versions("category-stable")
            self.assertEqual([item.is_current for item in versions], [False, True])

    def test_drafts_and_changes_filter_pending_records(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.save_draft(self._draft())
            guidance.save_draft(
                GuidanceDraft(
                    **{
                        **self._draft().to_dict(),
                        "draft_id": "draft-confirmed",
                        "status": "confirmed",
                    }
                )
            )
            self.assertEqual(
                [item.draft_id for item in guidance.list_pending_drafts()],
                ["draft-pending"],
            )

            guidance.save_change(self._change())
            self.assertEqual(
                [item.change_id for item in guidance.pending_changes()],
                ["change-stable"],
            )
            guidance.mark_change_processed("change-stable", NOW)
            self.assertEqual(guidance.pending_changes(), [])

    def test_transaction_rolls_back_cross_table_writes(self) -> None:
        with KnowledgeStore(self.db_path) as store:
            store.initialize()
            guidance = GuidanceStore(store)
            with self.assertRaises(RuntimeError):
                with store.transaction():
                    guidance.create_run(self._run())
                    guidance.save_batch(self._batch())
                    raise RuntimeError("stop")
            self.assertIsNone(guidance.get_run("run-stable"))
            self.assertEqual(guidance.list_batches("run-stable"), [])

    def _open_guidance_store(self) -> "GuidanceStoreContext":
        return GuidanceStoreContext(self.db_path)

    @staticmethod
    def _knowledge() -> KnowledgeRecord:
        return KnowledgeRecord(
            id="existing.knowledge",
            kind="module",
            title="已有知识",
            path=".project-kb/existing.md",
            ownership="curated",
            confidence="verified",
            content="保留内容",
        )

    @staticmethod
    def _run() -> GuidanceRun:
        return GuidanceRun(
            run_id="run-stable",
            project_root="/repo",
            snapshot_id="snapshot-1",
            status="scanning",
            total_files=2,
            covered_files=0,
            created_at=NOW,
            updated_at=NOW,
        )

    @staticmethod
    def _batch() -> GuidanceBatch:
        return GuidanceBatch(
            batch_id="batch-stable",
            run_id="run-stable",
            ordinal=0,
            status="pending",
            files=["src/a.py", "src/b.py"],
            snapshot_id="snapshot-1",
            created_at=NOW,
            updated_at=NOW,
        )

    @staticmethod
    def _category() -> GuidanceCategory:
        return GuidanceCategory(
            category_id="category-stable",
            run_id="run-stable",
            name="普通活动",
            purpose="指导同类活动开发",
            applies_to=["限时运营活动"],
            excludes=["登录流程"],
            samples=["src/a.py"],
            evidence=[{"path": "src/a.py", "hash": "sha256:" + "a" * 64}],
            confidence=0.9,
            unknowns=[],
            created_at=NOW,
            updated_at=NOW,
        )

    @staticmethod
    def _draft() -> GuidanceDraft:
        return GuidanceDraft(
            draft_id="draft-pending",
            run_id="run-stable",
            kind="category_catalog",
            status="awaiting_confirmation",
            path="/repo/.project-kb/功能分类目录-待审核.md",
            content_hash="sha256:" + "b" * 64,
            snapshot_id="snapshot-1",
            payload={"categories": ["category-stable"]},
            created_at=NOW,
            updated_at=NOW,
        )

    @staticmethod
    def _version(version_id: str, number: int, current: bool) -> GuidanceVersion:
        return GuidanceVersion(
            version_id=version_id,
            category_id="category-stable",
            version=number,
            title="普通活动开发指导",
            content="正文",
            content_hash="sha256:" + str(number) * 64,
            snapshot_id="snapshot-1",
            evidence=[{"path": "src/a.py"}],
            is_current=current,
            created_at=NOW,
        )

    @staticmethod
    def _change() -> GuidanceChange:
        return GuidanceChange(
            change_id="change-stable",
            base_snapshot_id="snapshot-1",
            head_snapshot_id="snapshot-2",
            update_level="guidance",
            changed_files=["src/a.py"],
            affected_categories=["category-stable"],
            payload={"reason": "开发步骤变化"},
            created_at=NOW,
        )


class GuidanceStoreContext:
    def __init__(self, path: Path):
        self.store = KnowledgeStore(path)

    def __enter__(self) -> GuidanceStore:
        self.store.initialize()
        return GuidanceStore(self.store)

    def __exit__(self, *args: object) -> None:
        self.store.close()


if __name__ == "__main__":
    unittest.main()
