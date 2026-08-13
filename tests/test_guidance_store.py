from __future__ import annotations

import math
import sqlite3
import tempfile
import unittest
from unittest import mock
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
            self.assertEqual(store.get_meta("schema_version"), "3")
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

    def test_numeric_fields_reject_coercion_bool_and_non_finite_values(self) -> None:
        cases = [
            (GuidanceRun, self._run().to_dict(), "total_files", ["2", True]),
            (GuidanceRun, self._run().to_dict(), "covered_files", ["0", False]),
            (GuidanceBatch, self._batch().to_dict(), "ordinal", ["0", False]),
            (GuidanceCategory, self._category().to_dict(), "confidence", ["0.9", True, math.nan, math.inf]),
            (GuidanceVersion, self._version("version-1", 1, True).to_dict(), "version", ["1", True]),
        ]
        for model_type, payload, field_name, invalid_values in cases:
            for invalid in invalid_values:
                with self.subTest(model=model_type.__name__, field=field_name, invalid=invalid):
                    with self.assertRaises(ValueError):
                        model_type.from_dict({**payload, field_name: invalid})
                    with self.assertRaises(ValueError):
                        model_type(**{**payload, field_name: invalid})
        with self.assertRaises(SchemaValidationError):
            validate_instance(
                {**self._category().to_dict(), "confidence": math.nan},
                GUIDANCE_CATEGORY_SCHEMA,
            )

    def test_json_storage_rejects_non_finite_numbers(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            invalid = GuidanceBatch(
                **{**self._batch().to_dict(), "result": {"confidence": math.nan}}
            )
            with self.assertRaises(ValueError):
                guidance.save_batch(invalid)

    def test_initialize_upgrades_early_v2_guidance_tables_without_data_loss(self) -> None:
        with KnowledgeStore(self.db_path) as store:
            store.initialize()
            guidance = GuidanceStore(store)
            with store.transaction():
                guidance.create_run(self._run())
                guidance.save_batch(self._batch())
                guidance.save_category(self._category())
                guidance.save_draft(self._draft())
                guidance.save_version(self._version("version-1", 1, True))
                guidance.save_change(self._change())
            expected = store.export_guidance_graph()
            store.connection.execute("PRAGMA foreign_keys = OFF")
            store.connection.execute("ALTER TABLE guidance_changes RENAME TO guidance_changes_current")
            store.connection.execute(
                """CREATE TABLE guidance_changes (
                    change_id TEXT PRIMARY KEY,
                    base_snapshot_id TEXT NOT NULL,
                    head_snapshot_id TEXT NOT NULL,
                    update_level TEXT NOT NULL,
                    changed_files_json TEXT NOT NULL,
                    affected_categories_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT
                )"""
            )
            store.connection.execute(
                """INSERT INTO guidance_changes(
                    change_id, base_snapshot_id, head_snapshot_id, update_level,
                    changed_files_json, affected_categories_json, payload_json,
                    created_at, processed_at
                ) SELECT change_id, base_snapshot_id, head_snapshot_id, update_level,
                    changed_files_json, affected_categories_json, payload_json,
                    created_at, processed_at FROM guidance_changes_current"""
            )
            store.connection.execute("DROP TABLE guidance_changes_current")
            store.connection.execute("PRAGMA foreign_keys = ON")
            store.connection.commit()

            store.initialize()
            actual = store.export_guidance_graph()
            self.assertEqual(actual["guidance_runs"], expected["guidance_runs"])
            self.assertEqual(actual["guidance_batches"], expected["guidance_batches"])
            self.assertEqual(actual["guidance_categories"], expected["guidance_categories"])
            self.assertEqual(actual["guidance_drafts"], expected["guidance_drafts"])
            self.assertEqual(actual["guidance_versions"], expected["guidance_versions"])
            self.assertEqual(
                actual["guidance_changes"][0]["project_root"],
                self._run().project_root,
            )
            self.assertEqual(actual["guidance_changes"][0]["change_id"], "change-stable")
            self.assertTrue(GuidanceStore(store).current_version("category-stable").is_current)

    def test_initialize_migrates_schema_v2_assets_to_project_guidance(self) -> None:
        with KnowledgeStore(self.db_path) as store:
            store.initialize()
            guidance = GuidanceStore(store)
            with store.transaction():
                guidance.create_run(self._run())
                guidance.save_category(self._category())
                guidance.save_draft(self._draft())
                guidance.save_version(self._version("version-1", 1, True))
            graph = store.export_guidance_graph()
            for row in graph["guidance_versions"]:
                row.pop("asset_type", None)
            store.connection.execute("PRAGMA foreign_keys = OFF")
            for table in (
                "guidance_versions", "guidance_drafts", "guidance_batches",
                "guidance_categories", "guidance_changes", "guidance_runs",
            ):
                store.connection.execute(f"DROP TABLE {table}")
            store.connection.executescript("""
                CREATE TABLE guidance_runs (run_id TEXT PRIMARY KEY, project_root TEXT NOT NULL, snapshot_id TEXT NOT NULL, status TEXT NOT NULL, total_files INTEGER NOT NULL, covered_files INTEGER NOT NULL, uncovered_files_json TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE guidance_batches (batch_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, status TEXT NOT NULL, files_json TEXT NOT NULL, snapshot_id TEXT NOT NULL, result_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE guidance_categories (category_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, name TEXT NOT NULL, purpose TEXT NOT NULL, applies_to_json TEXT NOT NULL, excludes_json TEXT NOT NULL, samples_json TEXT NOT NULL, evidence_json TEXT NOT NULL, confidence REAL NOT NULL, unknowns_json TEXT NOT NULL, relations_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE guidance_drafts (draft_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, category_id TEXT, kind TEXT NOT NULL CHECK(kind IN ('category_catalog', 'guidance')), status TEXT NOT NULL, path TEXT NOT NULL, content_hash TEXT NOT NULL, snapshot_id TEXT NOT NULL, payload_json TEXT NOT NULL, rejection_reason TEXT, confirmed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE guidance_versions (version_id TEXT PRIMARY KEY, category_id TEXT NOT NULL, draft_id TEXT, version INTEGER NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL, snapshot_id TEXT NOT NULL, evidence_json TEXT NOT NULL, is_current INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE guidance_changes (change_id TEXT PRIMARY KEY, project_root TEXT NOT NULL, base_snapshot_id TEXT NOT NULL, head_snapshot_id TEXT NOT NULL, update_level TEXT NOT NULL, changed_files_json TEXT NOT NULL, affected_categories_json TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, processed_at TEXT);
            """)
            store.import_guidance_graph(graph)
            store.set_meta("schema_version", "2")
            store.connection.execute("PRAGMA foreign_keys = ON")
            store.connection.commit()

            store.initialize()
            migrated = GuidanceStore(store).current_version("category-stable")
            self.assertEqual(migrated.version_id, "version-1")
            self.assertEqual(migrated.asset_type, "project_guidance")
            self.assertIsNone(GuidanceStore(store).current_version("category-stable", "methodology"))

    def test_initialize_rejects_future_schema_without_relabeling(self) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO metadata VALUES('schema_version', '4');"
        )
        connection.close()
        with KnowledgeStore(self.db_path) as store:
            with self.assertRaises(RuntimeError):
                store.initialize()
            self.assertEqual(store.get_meta("schema_version"), "4")

    def test_failed_migration_rolls_back_schema_and_version(self) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO metadata VALUES('schema_version', '1');"
        )
        connection.close()
        with KnowledgeStore(self.db_path) as store:
            def fail_after_partial_schema() -> None:
                store.connection.execute("CREATE TABLE guidance_partial(id TEXT)")
                raise sqlite3.OperationalError("migration failed")

            with mock.patch.object(store, "_create_guidance_schema", side_effect=fail_after_partial_schema):
                with self.assertRaises(sqlite3.OperationalError):
                    store.initialize()
            self.assertEqual(store.get_meta("schema_version"), "1")
            self.assertIsNone(store.connection.execute(
                "SELECT name FROM sqlite_master WHERE name='guidance_partial'"
            ).fetchone())

    def test_database_checks_reject_invalid_direct_sql(self) -> None:
        with KnowledgeStore(self.db_path) as store:
            store.initialize()
            guidance = GuidanceStore(store)
            guidance.create_run(self._run())
            guidance.save_category(self._category())
            invalid_statements = [
                ("UPDATE guidance_runs SET status='bad' WHERE run_id='run-stable'", ()),
                ("UPDATE guidance_runs SET total_files=-1 WHERE run_id='run-stable'", ()),
                ("UPDATE guidance_runs SET covered_files=3 WHERE run_id='run-stable'", ()),
                ("INSERT INTO guidance_batches(batch_id,run_id,ordinal,status,files_json,snapshot_id,created_at,updated_at) VALUES('bad-batch','run-stable',-1,'pending','[]','snapshot-1',?,?)", (NOW, NOW)),
                ("UPDATE guidance_categories SET confidence=2 WHERE category_id='category-stable'", ()),
                ("INSERT INTO guidance_drafts(draft_id,run_id,kind,status,path,content_hash,snapshot_id,payload_json,created_at,updated_at) VALUES('bad-draft','run-stable','bad','bad','/tmp/draft','sha256:' || printf('%064d',0),'snapshot-1','{}',?,?)", (NOW, NOW)),
                ("INSERT INTO guidance_versions(version_id,category_id,version,title,content,content_hash,snapshot_id,evidence_json,is_current,created_at) VALUES('bad-version','category-stable',0,'t','c','sha256:' || printf('%064d',0),'snapshot-1','[]',2,?)", (NOW,)),
                ("INSERT INTO guidance_changes(change_id,project_root,base_snapshot_id,head_snapshot_id,update_level,changed_files_json,affected_categories_json,payload_json,created_at) VALUES('','/repo','a','b','bad','[]','[]','{}',?)", (NOW,)),
            ]
            for sql, parameters in invalid_statements:
                with self.subTest(sql=sql):
                    with self.assertRaises(sqlite3.IntegrityError):
                        store.connection.execute(sql, parameters)

    def test_upserts_reject_stable_identity_changes_and_invalid_relations(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.create_run(GuidanceRun(**{**self._run().to_dict(), "run_id": "run-other", "snapshot_id": "snapshot-2"}))
            guidance.save_category(self._category())
            guidance.save_draft(self._draft())
            guidance.save_change(self._change())
            invalid_operations = [
                lambda: guidance.create_run(GuidanceRun(**{**self._run().to_dict(), "project_root": "/other"})),
                lambda: guidance.create_run(GuidanceRun(**{**self._run().to_dict(), "snapshot_id": "snapshot-2"})),
                lambda: guidance.save_batch(GuidanceBatch(**{**self._batch().to_dict(), "snapshot_id": "snapshot-2"})),
                lambda: guidance.save_category(GuidanceCategory(**{**self._category().to_dict(), "run_id": "run-other"})),
                lambda: guidance.save_draft(GuidanceDraft(**{**self._draft().to_dict(), "run_id": "run-other"})),
                lambda: guidance.save_draft(GuidanceDraft(**{**self._draft().to_dict(), "category_id": "missing"})),
                lambda: guidance.save_draft(GuidanceDraft(**{**self._draft().to_dict(), "kind": "guidance"})),
                lambda: guidance.save_change(GuidanceChange(**{**self._change().to_dict(), "project_root": "/other"})),
                lambda: guidance.save_change(GuidanceChange(**{**self._change().to_dict(), "base_snapshot_id": "snapshot-0"})),
                lambda: guidance.save_change(GuidanceChange(**{**self._change().to_dict(), "head_snapshot_id": "snapshot-3"})),
                lambda: guidance.save_change(GuidanceChange(**{**self._change().to_dict(), "update_level": "category"})),
            ]
            for operation in invalid_operations:
                with self.subTest(operation=operation):
                    with self.assertRaises((ValueError, KeyError, sqlite3.IntegrityError)):
                        operation()

    def test_draft_kind_category_and_version_snapshot_relations_are_consistent(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.save_category(self._category())
            invalid_drafts = [
                GuidanceDraft(**{**self._draft().to_dict(), "kind": "guidance"}),
                GuidanceDraft(**{**self._draft().to_dict(), "category_id": "category-stable"}),
            ]
            for draft in invalid_drafts:
                with self.subTest(draft=draft):
                    with self.assertRaises(ValueError):
                        guidance.save_draft(draft)
            with self.assertRaises(ValueError):
                guidance.save_version(GuidanceVersion(**{
                    **self._version("version-wrong-snapshot", 1, True).to_dict(),
                    "snapshot_id": "snapshot-2",
                }))

    def test_replaying_old_version_after_promotion_is_idempotent(self) -> None:
        with self._open_guidance_store() as guidance:
            guidance.create_run(self._run())
            guidance.save_category(self._category())
            old = self._version("version-1", 1, True)
            guidance.save_version(old)
            guidance.save_version(self._version("version-2", 2, True))
            replayed = guidance.save_version(old)
            self.assertFalse(replayed.is_current)
            self.assertEqual(guidance.current_version("category-stable").version_id, "version-2")

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
            project_root="/repo",
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
