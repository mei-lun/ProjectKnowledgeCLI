from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .engine import CodeIndexSnapshot
from .models import KnowledgeRecord, SourceReference


SCHEMA_VERSION = 4


class KnowledgeStore:
    def __init__(self, path: Path, readonly: bool = False):
        self.path = path
        self.readonly = readonly
        if readonly:
            uri = f"file:{path.as_posix()}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True, timeout=5)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        if not readonly:
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "KnowledgeStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def initialize(self) -> None:
        existing_version = self._existing_schema_version()
        if existing_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库 Schema v{existing_version} 高于当前支持的 v{SCHEMA_VERSION}"
            )
        if existing_version not in {0, 1, 2, 3, SCHEMA_VERSION}:
            raise RuntimeError(f"不支持从 Schema v{existing_version} 迁移")

        self.connection.commit()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._create_base_schema()
            self._upgrade_early_guidance_schema()
            self._create_guidance_schema()
            self._upgrade_guidance_asset_schema()
            try:
                self.connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                    "USING fts5(id UNINDEXED, title, content, tags)"
                )
                self.set_meta("fts", "enabled")
            except sqlite3.OperationalError:
                self.set_meta("fts", "disabled")
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _existing_schema_version(self) -> int:
        metadata = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone()
        if metadata is None:
            return 0
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            return 0
        try:
            version = int(row[0])
        except (TypeError, ValueError) as error:
            raise RuntimeError("数据库 schema_version 无效") from error
        if version < 0:
            raise RuntimeError("数据库 schema_version 无效")
        return version

    def _create_base_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                language TEXT NOT NULL,
                module TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                hash TEXT NOT NULL,
                parser TEXT NOT NULL,
                parse_error TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS symbols (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                line INTEGER NOT NULL,
                end_line INTEGER,
                signature TEXT NOT NULL,
                hash TEXT NOT NULL,
                confidence REAL NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)",
            "CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path)",
            """CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                line INTEGER,
                confidence REAL NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            )""",
            "CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source)",
            "CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target)",
            """CREATE TABLE IF NOT EXISTS routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method TEXT NOT NULL,
                route TEXT NOT NULL,
                handler TEXT NOT NULL,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                line INTEGER NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS knowledge (
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
            )""",
            """CREATE TABLE IF NOT EXISTS vector_documents (
                id TEXT PRIMARY KEY REFERENCES knowledge(id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                vector_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_vector_documents_provider ON vector_documents(provider_id, model_id, dimension)",
            """CREATE TABLE IF NOT EXISTS query_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                tool TEXT NOT NULL,
                input_size INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL
            )""",
        ]
        for statement in statements:
            self.connection.execute(statement)

    def _upgrade_early_guidance_schema(self) -> None:
        existing_tables = {
            row["name"]: row["sql"] or ""
            for row in self.connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name LIKE 'guidance_%'"
            )
        }
        if not existing_tables:
            return
        expected = set(self._guidance_tables())
        if set(existing_tables) != expected:
            raise RuntimeError("Guidance Schema 不完整，无法安全迁移")
        change_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(guidance_changes)")
        }
        if "project_root" in change_columns and all(
            "CHECK" in existing_tables[table].upper() for table in expected
        ):
            return

        graph = self.export_guidance_graph()
        project_roots = {
            row["project_root"] for row in graph["guidance_runs"] if row.get("project_root")
        }
        if len(project_roots) > 1:
            raise RuntimeError("Guidance 运行包含多个项目根，无法推断变化集归属")
        project_root = next(iter(project_roots), str(self.path.parent.parent.resolve()))
        for row in graph["guidance_changes"]:
            row.setdefault("project_root", project_root)

        for table in (
            "guidance_versions", "guidance_drafts", "guidance_batches",
            "guidance_categories", "guidance_changes", "guidance_runs",
        ):
            self.connection.execute(f"DROP TABLE {table}")
        self._create_guidance_schema()
        self.import_guidance_graph(graph)

    def _upgrade_guidance_asset_schema(self) -> None:
        draft_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='guidance_drafts'"
        ).fetchone()
        version_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(guidance_versions)")
        }
        needs_upgrade = bool(
            draft_sql and (
                "'methodology'" not in (draft_sql["sql"] or "")
                or "asset_type" not in version_columns
            )
        )
        if not needs_upgrade:
            return
        graph = self.export_guidance_graph()
        for row in graph["guidance_versions"]:
            row.setdefault("asset_type", "project_guidance")
        for table in (
            "guidance_versions", "guidance_drafts", "guidance_batches",
            "guidance_categories", "guidance_changes", "guidance_runs",
        ):
            self.connection.execute(f"DROP TABLE {table}")
        self._create_guidance_schema()
        self.import_guidance_graph(graph)

    def _create_guidance_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS guidance_runs (
                run_id TEXT PRIMARY KEY CHECK(length(run_id) > 0),
                project_root TEXT NOT NULL CHECK(length(project_root) > 0),
                snapshot_id TEXT NOT NULL CHECK(length(snapshot_id) > 0),
                status TEXT NOT NULL CHECK(status IN (
                    'scanning', 'category_review', 'categories_confirmed',
                    'guidance_generation', 'guidance_review', 'complete', 'failed'
                )),
                total_files INTEGER NOT NULL CHECK(
                    typeof(total_files) = 'integer' AND total_files >= 0
                ),
                covered_files INTEGER NOT NULL CHECK(
                    typeof(covered_files) = 'integer' AND covered_files >= 0
                    AND covered_files <= total_files
                ),
                uncovered_files_json TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS guidance_batches (
                batch_id TEXT PRIMARY KEY CHECK(length(batch_id) > 0),
                run_id TEXT NOT NULL REFERENCES guidance_runs(run_id) ON DELETE CASCADE
                    CHECK(length(run_id) > 0),
                ordinal INTEGER NOT NULL CHECK(typeof(ordinal) = 'integer' AND ordinal >= 0),
                status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
                files_json TEXT NOT NULL,
                snapshot_id TEXT NOT NULL CHECK(length(snapshot_id) > 0),
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, ordinal)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_guidance_batches_pending
                ON guidance_batches(run_id, status, ordinal)""",
            """CREATE TABLE IF NOT EXISTS guidance_categories (
                category_id TEXT PRIMARY KEY CHECK(length(category_id) > 0),
                run_id TEXT NOT NULL REFERENCES guidance_runs(run_id) ON DELETE CASCADE
                    CHECK(length(run_id) > 0),
                name TEXT NOT NULL CHECK(length(name) > 0),
                purpose TEXT NOT NULL,
                applies_to_json TEXT NOT NULL,
                excludes_json TEXT NOT NULL,
                samples_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                confidence REAL NOT NULL CHECK(
                    typeof(confidence) IN ('real', 'integer')
                    AND confidence >= 0 AND confidence <= 1
                ),
                unknowns_json TEXT NOT NULL,
                relations_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_guidance_categories_run
                ON guidance_categories(run_id, category_id)""",
            """CREATE TABLE IF NOT EXISTS guidance_drafts (
                draft_id TEXT PRIMARY KEY CHECK(length(draft_id) > 0),
                run_id TEXT NOT NULL REFERENCES guidance_runs(run_id) ON DELETE CASCADE
                    CHECK(length(run_id) > 0),
                category_id TEXT REFERENCES guidance_categories(category_id) ON DELETE SET NULL
                    CHECK(category_id IS NULL OR length(category_id) > 0),
                kind TEXT NOT NULL CHECK(kind IN ('category_catalog', 'methodology', 'guidance')),
                status TEXT NOT NULL CHECK(status IN (
                    'incomplete', 'awaiting_confirmation', 'confirmed', 'rejected'
                )),
                path TEXT NOT NULL CHECK(length(path) > 0),
                content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
                snapshot_id TEXT NOT NULL CHECK(length(snapshot_id) > 0),
                payload_json TEXT NOT NULL,
                rejection_reason TEXT,
                confirmed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_guidance_drafts_pending
                ON guidance_drafts(status, run_id, category_id)""",
            """CREATE TABLE IF NOT EXISTS guidance_versions (
                version_id TEXT PRIMARY KEY CHECK(length(version_id) > 0),
                category_id TEXT NOT NULL REFERENCES guidance_categories(category_id) ON DELETE CASCADE
                    CHECK(length(category_id) > 0),
                draft_id TEXT REFERENCES guidance_drafts(draft_id) ON DELETE SET NULL
                    CHECK(draft_id IS NULL OR length(draft_id) > 0),
                version INTEGER NOT NULL CHECK(typeof(version) = 'integer' AND version > 0),
                title TEXT NOT NULL CHECK(length(title) > 0),
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
                snapshot_id TEXT NOT NULL CHECK(length(snapshot_id) > 0),
                evidence_json TEXT NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 0 CHECK(is_current IN (0, 1)),
                asset_type TEXT NOT NULL DEFAULT 'project_guidance'
                    CHECK(asset_type IN ('methodology', 'project_guidance')),
                created_at TEXT NOT NULL,
                UNIQUE(category_id, asset_type, version)
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_guidance_versions_current
                ON guidance_versions(category_id, asset_type) WHERE is_current = 1""",
            """CREATE TABLE IF NOT EXISTS guidance_changes (
                change_id TEXT PRIMARY KEY CHECK(length(change_id) > 0),
                project_root TEXT NOT NULL CHECK(length(project_root) > 0),
                base_snapshot_id TEXT NOT NULL CHECK(length(base_snapshot_id) > 0),
                head_snapshot_id TEXT NOT NULL CHECK(length(head_snapshot_id) > 0),
                update_level TEXT NOT NULL CHECK(update_level IN ('fact', 'guidance', 'category')),
                changed_files_json TEXT NOT NULL,
                affected_categories_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                processed_at TEXT
            )""",
            """CREATE INDEX IF NOT EXISTS idx_guidance_changes_pending
                ON guidance_changes(processed_at, created_at)""",
            """CREATE TABLE IF NOT EXISTS task_completions (
                task_id TEXT PRIMARY KEY CHECK(length(task_id) > 0),
                project_root TEXT NOT NULL CHECK(length(project_root) > 0),
                summary TEXT NOT NULL CHECK(length(summary) > 0),
                changed_files_json TEXT NOT NULL,
                changed_symbols_json TEXT NOT NULL,
                tests_json TEXT NOT NULL,
                base_snapshot_id TEXT NOT NULL CHECK(length(base_snapshot_id) > 0),
                final_snapshot_id TEXT NOT NULL CHECK(length(final_snapshot_id) > 0),
                user_confirmed INTEGER NOT NULL CHECK(user_confirmed IN (0, 1)),
                generation_status TEXT NOT NULL CHECK(generation_status IN ('pending', 'generated', 'skipped', 'failed')),
                affected_categories_json TEXT NOT NULL,
                skip_reason TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_task_completions_status
                ON task_completions(generation_status, updated_at)""",
        ]
        for statement in statements:
            self.connection.execute(statement)

    def export_guidance_graph(self) -> dict[str, list[dict[str, Any]]]:
        graph: dict[str, list[dict[str, Any]]] = {}
        for table in self._guidance_tables():
            exists = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            graph[table] = self.rows(f"SELECT * FROM {table}") if exists else []
        exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_completions'"
        ).fetchone()
        graph["task_completions"] = self.rows("SELECT * FROM task_completions") if exists else []
        return graph

    def import_guidance_graph(self, graph: dict[str, list[dict[str, Any]]]) -> None:
        for table in self._guidance_tables():
            for row in graph.get(table, []):
                columns = list(row)
                placeholders = ", ".join("?" for _ in columns)
                self.connection.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    [row[column] for column in columns],
                )
        for row in graph.get("task_completions", []):
            columns = list(row)
            placeholders = ", ".join("?" for _ in columns)
            self.connection.execute(
                f"INSERT INTO task_completions ({', '.join(columns)}) VALUES ({placeholders})",
                [row[column] for column in columns],
            )

    @staticmethod
    def _guidance_tables() -> tuple[str, ...]:
        return (
            "guidance_runs", "guidance_batches", "guidance_categories",
            "guidance_drafts", "guidance_versions", "guidance_changes",
        )

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def metadata(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.connection.execute("SELECT key, value FROM metadata")}

    def file_hashes(self) -> dict[str, str]:
        return {row["path"]: row["hash"] for row in self.connection.execute("SELECT path, hash FROM files")}

    def replace_code_snapshot(self, snapshot: CodeIndexSnapshot) -> None:
        self.connection.execute("DELETE FROM routes")
        self.connection.execute("DELETE FROM relations")
        self.connection.execute("DELETE FROM symbols")
        self.connection.execute("DELETE FROM files")
        self.connection.executemany(
            "INSERT INTO files(path, language, module, size, mtime_ns, hash, parser, parse_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item.path,
                    item.language,
                    item.module,
                    item.size,
                    item.mtime_ns,
                    item.content_hash,
                    "codegraph-snapshot",
                    None,
                )
                for item in snapshot.files
            ],
        )
        self.set_meta("codegraph_snapshot_id", snapshot.snapshot_id)

    def delete_files(self, paths: Iterable[str]) -> None:
        self.connection.executemany("DELETE FROM files WHERE path = ?", [(path,) for path in paths])

    def upsert_knowledge(self, record: KnowledgeRecord) -> None:
        values = (
            record.id,
            record.kind,
            record.title,
            record.path,
            record.ownership,
            record.confidence,
            record.status,
            record.content,
            record.source_commit,
            json.dumps(record.source_hashes, ensure_ascii=False, sort_keys=True),
            json.dumps([source.to_dict() for source in record.sources], ensure_ascii=False),
            json.dumps(record.tags, ensure_ascii=False),
            json.dumps(record.supersedes, ensure_ascii=False),
            record.last_generated_at,
            record.last_verified_at,
        )
        self.connection.execute(
            """
            INSERT INTO knowledge(id, kind, title, path, ownership, confidence, status, content, source_commit,
                                  source_hashes_json, sources_json, tags_json, supersedes_json, last_generated_at, last_verified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, title=excluded.title, path=excluded.path,
                ownership=excluded.ownership, confidence=excluded.confidence, status=excluded.status,
                content=excluded.content, source_commit=excluded.source_commit,
                source_hashes_json=excluded.source_hashes_json, sources_json=excluded.sources_json,
                tags_json=excluded.tags_json, supersedes_json=excluded.supersedes_json,
                last_generated_at=excluded.last_generated_at, last_verified_at=excluded.last_verified_at
            """,
            values,
        )
        if self.get_meta("fts") == "enabled":
            self.connection.execute("DELETE FROM knowledge_fts WHERE id = ?", (record.id,))
            self.connection.execute(
                "INSERT INTO knowledge_fts(id, title, content, tags) VALUES (?, ?, ?, ?)",
                (record.id, record.title, record.content, " ".join(record.tags)),
            )

    def delete_missing_knowledge(self, ids: set[str], ownership: str) -> None:
        rows = self.connection.execute("SELECT id FROM knowledge WHERE ownership = ?", (ownership,)).fetchall()
        for row in rows:
            if row["id"] not in ids:
                self.connection.execute("DELETE FROM knowledge WHERE id = ?", (row["id"],))
                if self.get_meta("fts") == "enabled":
                    self.connection.execute("DELETE FROM knowledge_fts WHERE id = ?", (row["id"],))

    def delete_knowledge(self, record_id: str) -> None:
        self.connection.execute("DELETE FROM knowledge WHERE id = ?", (record_id,))
        if self.get_meta("fts") == "enabled":
            self.connection.execute("DELETE FROM knowledge_fts WHERE id = ?", (record_id,))

    @staticmethod
    def _knowledge(row: sqlite3.Row) -> KnowledgeRecord:
        sources = [SourceReference(**source) for source in json.loads(row["sources_json"])]
        return KnowledgeRecord(
            id=row["id"], kind=row["kind"], title=row["title"], path=row["path"], ownership=row["ownership"],
            confidence=row["confidence"], status=row["status"], sources=sources, source_commit=row["source_commit"],
            source_hashes=json.loads(row["source_hashes_json"]), last_generated_at=row["last_generated_at"],
            last_verified_at=row["last_verified_at"], supersedes=json.loads(row["supersedes_json"]),
            tags=json.loads(row["tags_json"]), content=row["content"],
        )

    def get_knowledge(self, record_id: str) -> KnowledgeRecord | None:
        row = self.connection.execute("SELECT * FROM knowledge WHERE id = ?", (record_id,)).fetchone()
        return self._knowledge(row) if row else None

    def all_knowledge(self) -> list[KnowledgeRecord]:
        return [self._knowledge(row) for row in self.connection.execute("SELECT * FROM knowledge ORDER BY id")]

    def search_knowledge(self, query: str, limit: int = 10, kinds: list[str] | None = None, module: str | None = None) -> list[tuple[KnowledgeRecord, float]]:
        filters: list[str] = []
        parameters: list[Any] = []
        if kinds:
            filters.append("k.kind IN (%s)" % ",".join("?" for _ in kinds))
            parameters.extend(kinds)
        if module:
            filters.append("(k.tags_json LIKE ? OR k.path LIKE ?)")
            parameters.extend([f'%"{module}"%', f"%/{module}%"])
        where = " AND " + " AND ".join(filters) if filters else ""
        rows: list[sqlite3.Row]
        if query.strip() and self.get_meta("fts") == "enabled":
            terms = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in query.split() if term)
            try:
                rows = list(self.connection.execute(
                    f"SELECT k.*, -bm25(knowledge_fts) AS score FROM knowledge_fts JOIN knowledge k USING(id) WHERE knowledge_fts MATCH ?{where} ORDER BY score DESC LIMIT ?",
                    [terms, *parameters, limit],
                ))
            except sqlite3.OperationalError:
                rows = []
        else:
            rows = []
        if not rows:
            like = f"%{query}%"
            rows = list(self.connection.execute(
                f"SELECT k.*, CASE WHEN k.title LIKE ? THEN 2.0 ELSE 1.0 END AS score FROM knowledge k WHERE (k.title LIKE ? OR k.content LIKE ? OR k.tags_json LIKE ?){where} ORDER BY score DESC, k.id LIMIT ?",
                [like, like, like, like, *parameters, limit],
            ))
        return [(self._knowledge(row), float(row["score"])) for row in rows]

    def counts(self) -> dict[str, int]:
        counts = {
            "files": int(self.connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
            "knowledge": int(self.connection.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]),
            # Legacy tables remain in the on-disk schema for upgrade compatibility,
            # but CodeGraph is authoritative and runtime code never reads them.
            "symbols": 0,
            "relations": 0,
            "routes": 0,
        }
        counts["modules"] = int(self.connection.execute("SELECT COUNT(DISTINCT module) FROM files").fetchone()[0])
        counts["parse_errors"] = int(self.connection.execute("SELECT COUNT(*) FROM files WHERE parse_error IS NOT NULL").fetchone()[0])
        counts["unresolved_relations"] = 0
        counts["stale_knowledge"] = int(self.connection.execute("SELECT COUNT(*) FROM knowledge WHERE status IN ('stale', 'potentially_stale')").fetchone()[0])
        counts["conflicted_knowledge"] = int(self.connection.execute("SELECT COUNT(*) FROM knowledge WHERE status = 'conflicted'").fetchone()[0])
        return counts

    def rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(sql, tuple(parameters))]

    def record_query(self, created_at: str, tool: str, input_size: int, output_tokens: int, duration_ms: int) -> None:
        if self.readonly:
            return
        self.connection.execute(
            "INSERT INTO query_stats(created_at, tool, input_size, output_tokens, duration_ms) VALUES (?, ?, ?, ?, ?)",
            (created_at, tool, input_size, output_tokens, duration_ms),
        )
        self.connection.commit()
