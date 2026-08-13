from __future__ import annotations

import json
import sqlite3

from .guidance_models import (
    GuidanceBatch,
    GuidanceCategory,
    GuidanceChange,
    GuidanceDraft,
    GuidanceRun,
    GuidanceVersion,
)
from .store import SCHEMA_VERSION, KnowledgeStore


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class GuidanceStore:
    """Read and write guidance workflow records through an initialized store."""

    def __init__(self, store: KnowledgeStore):
        self.store = store
        self.connection = store.connection
        try:
            if store.get_meta("schema_version") != str(SCHEMA_VERSION):
                raise RuntimeError(
                    f"KnowledgeStore 尚未初始化为 Schema v{SCHEMA_VERSION}"
                )
        except sqlite3.OperationalError as error:
            raise RuntimeError("KnowledgeStore 尚未初始化") from error

    def create_run(self, run: GuidanceRun) -> GuidanceRun:
        cursor = self.connection.execute(
            """
            INSERT INTO guidance_runs(
                run_id, project_root, snapshot_id, status, total_files, covered_files,
                uncovered_files_json, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status, total_files=excluded.total_files,
                covered_files=excluded.covered_files,
                uncovered_files_json=excluded.uncovered_files_json,
                error=excluded.error, updated_at=excluded.updated_at
            WHERE guidance_runs.project_root=excluded.project_root
              AND guidance_runs.snapshot_id=excluded.snapshot_id
              AND guidance_runs.created_at=excluded.created_at
            """,
            (
                run.run_id, run.project_root, run.snapshot_id, run.status,
                run.total_files, run.covered_files, _json(run.uncovered_files),
                run.error, run.created_at, run.updated_at,
            ),
        )
        self._require_upsert(cursor, "run_id 已绑定到其他项目或快照")
        return run

    def get_run(self, run_id: str) -> GuidanceRun | None:
        row = self.connection.execute(
            "SELECT * FROM guidance_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return GuidanceRun.from_dict({
            "run_id": row["run_id"], "project_root": row["project_root"],
            "snapshot_id": row["snapshot_id"], "status": row["status"],
            "total_files": row["total_files"], "covered_files": row["covered_files"],
            "uncovered_files": json.loads(row["uncovered_files_json"]),
            "error": row["error"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })

    def save_batch(self, batch: GuidanceBatch) -> GuidanceBatch:
        existing = self.connection.execute(
            "SELECT run_id, ordinal, snapshot_id FROM guidance_batches WHERE batch_id = ?",
            (batch.batch_id,),
        ).fetchone()
        if existing is not None and (
            existing["run_id"] != batch.run_id
            or existing["ordinal"] != batch.ordinal
            or existing["snapshot_id"] != batch.snapshot_id
        ):
            raise ValueError("batch_id 已绑定到其他运行、序号或快照")
        run = self._require_run(batch.run_id)
        if run.snapshot_id != batch.snapshot_id:
            raise ValueError("批次快照与运行快照不一致")
        cursor = self.connection.execute(
            """
            INSERT INTO guidance_batches(
                batch_id, run_id, ordinal, status, files_json, snapshot_id,
                result_json, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(batch_id) DO UPDATE SET
                status=excluded.status, result_json=excluded.result_json,
                error=excluded.error, updated_at=excluded.updated_at
            WHERE guidance_batches.run_id=excluded.run_id
              AND guidance_batches.ordinal=excluded.ordinal
              AND guidance_batches.files_json=excluded.files_json
              AND guidance_batches.snapshot_id=excluded.snapshot_id
              AND guidance_batches.created_at=excluded.created_at
            """,
            (
                batch.batch_id, batch.run_id, batch.ordinal, batch.status,
                _json(batch.files), batch.snapshot_id,
                _json(batch.result) if batch.result is not None else None,
                batch.error, batch.created_at, batch.updated_at,
            ),
        )
        self._require_upsert(cursor, "batch_id 已绑定到其他运行、序号或快照")
        return batch

    def next_pending_batch(self, run_id: str) -> GuidanceBatch | None:
        row = self.connection.execute(
            """
            SELECT * FROM guidance_batches
            WHERE run_id = ? AND status = 'pending'
            ORDER BY ordinal, batch_id LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return self._batch(row) if row else None

    def list_batches(self, run_id: str) -> list[GuidanceBatch]:
        rows = self.connection.execute(
            "SELECT * FROM guidance_batches WHERE run_id = ? ORDER BY ordinal, batch_id",
            (run_id,),
        )
        return [self._batch(row) for row in rows]

    def save_category(self, category: GuidanceCategory) -> GuidanceCategory:
        self._require_run(category.run_id)
        cursor = self.connection.execute(
            """
            INSERT INTO guidance_categories(
                category_id, run_id, name, purpose, applies_to_json, excludes_json,
                samples_json, evidence_json, confidence, unknowns_json,
                relations_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(category_id) DO UPDATE SET
                name=excluded.name, purpose=excluded.purpose,
                applies_to_json=excluded.applies_to_json, excludes_json=excluded.excludes_json,
                samples_json=excluded.samples_json, evidence_json=excluded.evidence_json,
                confidence=excluded.confidence, unknowns_json=excluded.unknowns_json,
                relations_json=excluded.relations_json, updated_at=excluded.updated_at
            WHERE guidance_categories.run_id=excluded.run_id
              AND guidance_categories.created_at=excluded.created_at
            """,
            (
                category.category_id, category.run_id, category.name, category.purpose,
                _json(category.applies_to), _json(category.excludes), _json(category.samples),
                _json(category.evidence), category.confidence, _json(category.unknowns),
                _json(category.relations), category.created_at, category.updated_at,
            ),
        )
        self._require_upsert(cursor, "category_id 已绑定到其他运行")
        return category

    def list_categories(self, run_id: str | None = None) -> list[GuidanceCategory]:
        if run_id is None:
            rows = self.connection.execute(
                "SELECT * FROM guidance_categories ORDER BY category_id"
            )
        else:
            rows = self.connection.execute(
                "SELECT * FROM guidance_categories WHERE run_id = ? ORDER BY category_id",
                (run_id,),
            )
        return [self._category(row) for row in rows]

    def save_draft(self, draft: GuidanceDraft) -> GuidanceDraft:
        run = self._require_run(draft.run_id)
        if run.snapshot_id != draft.snapshot_id:
            raise ValueError("草稿快照与运行快照不一致")
        if draft.kind in {"methodology", "guidance"} and draft.category_id is None:
            raise ValueError("方法论或项目指导草稿必须关联类别")
        if draft.kind == "category_catalog" and draft.category_id is not None:
            raise ValueError("分类目录草稿不能关联单一类别")
        if draft.category_id is not None:
            category = self._require_category(draft.category_id)
            category_run = self._require_run(category.run_id)
            if category_run.project_root != run.project_root:
                raise ValueError("草稿类别与运行不属于同一项目")
        cursor = self.connection.execute(
            """
            INSERT INTO guidance_drafts(
                draft_id, run_id, category_id, kind, status, path, content_hash,
                snapshot_id, payload_json, rejection_reason, confirmed_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(draft_id) DO UPDATE SET
                status=excluded.status, path=excluded.path,
                content_hash=excluded.content_hash, payload_json=excluded.payload_json,
                rejection_reason=excluded.rejection_reason,
                confirmed_at=excluded.confirmed_at, updated_at=excluded.updated_at
            WHERE guidance_drafts.run_id=excluded.run_id
              AND guidance_drafts.category_id IS excluded.category_id
              AND guidance_drafts.kind=excluded.kind
              AND guidance_drafts.snapshot_id=excluded.snapshot_id
              AND guidance_drafts.created_at=excluded.created_at
            """,
            (
                draft.draft_id, draft.run_id, draft.category_id, draft.kind,
                draft.status, draft.path, draft.content_hash, draft.snapshot_id,
                _json(draft.payload), draft.rejection_reason, draft.confirmed_at,
                draft.created_at, draft.updated_at,
            ),
        )
        self._require_upsert(cursor, "draft_id 已绑定到其他运行、类别、类型或快照")
        return draft

    def get_draft(self, draft_id: str) -> GuidanceDraft | None:
        row = self.connection.execute(
            "SELECT * FROM guidance_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        return self._draft(row) if row else None

    def list_pending_drafts(
        self, run_id: str | None = None, category_id: str | None = None
    ) -> list[GuidanceDraft]:
        clauses = ["status IN ('incomplete', 'awaiting_confirmation')"]
        parameters: list[str] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if category_id is not None:
            clauses.append("category_id = ?")
            parameters.append(category_id)
        rows = self.connection.execute(
            f"SELECT * FROM guidance_drafts WHERE {' AND '.join(clauses)} ORDER BY created_at, draft_id",
            parameters,
        )
        return [self._draft(row) for row in rows]

    def save_version(self, version: GuidanceVersion) -> GuidanceVersion:
        category = self._require_category(version.category_id)
        run = self._require_run(category.run_id)
        snapshot_run = self.connection.execute(
            "SELECT 1 FROM guidance_runs WHERE project_root=? AND snapshot_id=? LIMIT 1",
            (run.project_root, version.snapshot_id),
        ).fetchone()
        if snapshot_run is None:
            raise ValueError("版本快照不属于类别所在项目的已知运行")
        if version.draft_id is not None:
            draft = self.get_draft(version.draft_id)
            if draft is None:
                raise KeyError(f"草稿不存在：{version.draft_id}")
            if draft.category_id != version.category_id:
                raise ValueError("版本草稿与类别不一致")
            expected_kind = "methodology" if version.asset_type == "methodology" else "guidance"
            if draft.kind != expected_kind:
                raise ValueError("版本资产类型与草稿类型不一致")
        existing = self.connection.execute(
            "SELECT * FROM guidance_versions WHERE version_id = ?",
            (version.version_id,),
        ).fetchone()
        if existing is not None:
            stored = self._version(existing)
            if self._version_identity(stored) != self._version_identity(version):
                raise ValueError("正式指导版本不可修改")
            return stored

        self.connection.execute("SAVEPOINT save_guidance_version")
        try:
            if version.is_current:
                self.connection.execute(
                    "UPDATE guidance_versions SET is_current = 0 WHERE category_id = ? AND asset_type = ?",
                    (version.category_id, version.asset_type),
                )
            self.connection.execute(
                """
                INSERT INTO guidance_versions(
                    version_id, category_id, draft_id, version, title, content,
                    content_hash, snapshot_id, evidence_json, is_current, created_at, asset_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.version_id, version.category_id, version.draft_id,
                    version.version, version.title, version.content, version.content_hash,
                    version.snapshot_id, _json(version.evidence), int(version.is_current),
                    version.created_at, version.asset_type,
                ),
            )
        except BaseException:
            self.connection.execute("ROLLBACK TO save_guidance_version")
            self.connection.execute("RELEASE save_guidance_version")
            raise
        self.connection.execute("RELEASE save_guidance_version")
        return version

    def current_version(
        self, category_id: str, asset_type: str = "project_guidance"
    ) -> GuidanceVersion | None:
        row = self.connection.execute(
            """
            SELECT * FROM guidance_versions
            WHERE category_id = ? AND asset_type = ? AND is_current = 1
            """,
            (category_id, asset_type),
        ).fetchone()
        return self._version(row) if row else None

    def list_versions(
        self, category_id: str, asset_type: str = "project_guidance"
    ) -> list[GuidanceVersion]:
        rows = self.connection.execute(
            """
            SELECT * FROM guidance_versions
            WHERE category_id = ? AND asset_type = ? ORDER BY version, version_id
            """,
            (category_id, asset_type),
        )
        return [self._version(row) for row in rows]

    def save_change(self, change: GuidanceChange) -> GuidanceChange:
        cursor = self.connection.execute(
            """
            INSERT INTO guidance_changes(
                change_id, project_root, base_snapshot_id, head_snapshot_id, update_level,
                changed_files_json, affected_categories_json, payload_json,
                created_at, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(change_id) DO UPDATE SET
                changed_files_json=excluded.changed_files_json,
                affected_categories_json=excluded.affected_categories_json,
                payload_json=excluded.payload_json, processed_at=excluded.processed_at
            WHERE guidance_changes.project_root=excluded.project_root
              AND guidance_changes.base_snapshot_id=excluded.base_snapshot_id
              AND guidance_changes.head_snapshot_id=excluded.head_snapshot_id
              AND guidance_changes.update_level=excluded.update_level
              AND guidance_changes.created_at=excluded.created_at
            """,
            (
                change.change_id, change.project_root, change.base_snapshot_id,
                change.head_snapshot_id, change.update_level, _json(change.changed_files),
                _json(change.affected_categories), _json(change.payload),
                change.created_at, change.processed_at,
            ),
        )
        self._require_upsert(cursor, "change_id 已绑定到其他项目、快照或更新级别")
        return change

    def pending_changes(self) -> list[GuidanceChange]:
        rows = self.connection.execute(
            """
            SELECT * FROM guidance_changes WHERE processed_at IS NULL
            ORDER BY created_at, change_id
            """
        )
        return [self._change(row) for row in rows]

    def mark_change_processed(self, change_id: str, processed_at: str) -> None:
        GuidanceChange.from_dict({
            "change_id": change_id,
            "project_root": "validation",
            "base_snapshot_id": "validation",
            "head_snapshot_id": "validation",
            "update_level": "fact",
            "changed_files": [],
            "affected_categories": [],
            "payload": {},
            "created_at": processed_at,
            "processed_at": processed_at,
        })
        cursor = self.connection.execute(
            "UPDATE guidance_changes SET processed_at = ? WHERE change_id = ?",
            (processed_at, change_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"变化集不存在：{change_id}")

    def _require_run(self, run_id: str) -> GuidanceRun:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"运行不存在：{run_id}")
        return run

    def _require_category(self, category_id: str) -> GuidanceCategory:
        row = self.connection.execute(
            "SELECT * FROM guidance_categories WHERE category_id = ?", (category_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"类别不存在：{category_id}")
        return self._category(row)

    @staticmethod
    def _require_upsert(cursor: sqlite3.Cursor, message: str) -> None:
        if cursor.rowcount == 0:
            raise ValueError(message)

    @staticmethod
    def _version_identity(version: GuidanceVersion) -> tuple[object, ...]:
        return (
            version.version_id, version.category_id, version.draft_id, version.version,
            version.title, version.content, version.content_hash, version.snapshot_id,
            _json(version.evidence), version.created_at, version.asset_type,
        )

    @staticmethod
    def _batch(row: sqlite3.Row) -> GuidanceBatch:
        return GuidanceBatch.from_dict({
            "batch_id": row["batch_id"], "run_id": row["run_id"],
            "ordinal": row["ordinal"], "status": row["status"],
            "files": json.loads(row["files_json"]), "snapshot_id": row["snapshot_id"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })

    @staticmethod
    def _category(row: sqlite3.Row) -> GuidanceCategory:
        return GuidanceCategory.from_dict({
            "category_id": row["category_id"], "run_id": row["run_id"],
            "name": row["name"], "purpose": row["purpose"],
            "applies_to": json.loads(row["applies_to_json"]),
            "excludes": json.loads(row["excludes_json"]),
            "samples": json.loads(row["samples_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "confidence": row["confidence"], "unknowns": json.loads(row["unknowns_json"]),
            "relations": json.loads(row["relations_json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })

    @staticmethod
    def _draft(row: sqlite3.Row) -> GuidanceDraft:
        return GuidanceDraft.from_dict({
            "draft_id": row["draft_id"], "run_id": row["run_id"],
            "category_id": row["category_id"], "kind": row["kind"],
            "status": row["status"], "path": row["path"],
            "content_hash": row["content_hash"], "snapshot_id": row["snapshot_id"],
            "payload": json.loads(row["payload_json"]),
            "rejection_reason": row["rejection_reason"],
            "confirmed_at": row["confirmed_at"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })

    @staticmethod
    def _version(row: sqlite3.Row) -> GuidanceVersion:
        return GuidanceVersion.from_dict({
            "version_id": row["version_id"], "category_id": row["category_id"],
            "draft_id": row["draft_id"], "version": row["version"],
            "title": row["title"], "content": row["content"],
            "content_hash": row["content_hash"], "snapshot_id": row["snapshot_id"],
            "evidence": json.loads(row["evidence_json"]),
            "is_current": bool(row["is_current"]), "created_at": row["created_at"],
            "asset_type": row["asset_type"],
        })

    @staticmethod
    def _change(row: sqlite3.Row) -> GuidanceChange:
        return GuidanceChange.from_dict({
            "change_id": row["change_id"], "project_root": row["project_root"],
            "base_snapshot_id": row["base_snapshot_id"],
            "head_snapshot_id": row["head_snapshot_id"],
            "update_level": row["update_level"],
            "changed_files": json.loads(row["changed_files_json"]),
            "affected_categories": json.loads(row["affected_categories_json"]),
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"], "processed_at": row["processed_at"],
        })
