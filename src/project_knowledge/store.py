from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .engine import IndexedFile
from .models import KnowledgeRecord, ParseResult, SourceReference


SCHEMA_VERSION = 2


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
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                language TEXT NOT NULL,
                module TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                hash TEXT NOT NULL,
                parser TEXT NOT NULL,
                parse_error TEXT
            );
            CREATE TABLE IF NOT EXISTS symbols (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                line INTEGER NOT NULL,
                end_line INTEGER,
                signature TEXT NOT NULL,
                hash TEXT NOT NULL,
                confidence REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
            CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                line INTEGER,
                confidence REAL NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);
            CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target);
            CREATE TABLE IF NOT EXISTS routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method TEXT NOT NULL,
                route TEXT NOT NULL,
                handler TEXT NOT NULL,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                line INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge (
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
            CREATE TABLE IF NOT EXISTS query_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                tool TEXT NOT NULL,
                input_size INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guidance_runs (
                run_id TEXT PRIMARY KEY,
                project_root TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                status TEXT NOT NULL,
                total_files INTEGER NOT NULL,
                covered_files INTEGER NOT NULL,
                uncovered_files_json TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guidance_batches (
                batch_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES guidance_runs(run_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                status TEXT NOT NULL,
                files_json TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, ordinal)
            );
            CREATE INDEX IF NOT EXISTS idx_guidance_batches_pending
                ON guidance_batches(run_id, status, ordinal);
            CREATE TABLE IF NOT EXISTS guidance_categories (
                category_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES guidance_runs(run_id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                purpose TEXT NOT NULL,
                applies_to_json TEXT NOT NULL,
                excludes_json TEXT NOT NULL,
                samples_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                unknowns_json TEXT NOT NULL,
                relations_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_guidance_categories_run
                ON guidance_categories(run_id, category_id);
            CREATE TABLE IF NOT EXISTS guidance_drafts (
                draft_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES guidance_runs(run_id) ON DELETE CASCADE,
                category_id TEXT REFERENCES guidance_categories(category_id) ON DELETE SET NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                rejection_reason TEXT,
                confirmed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_guidance_drafts_pending
                ON guidance_drafts(status, run_id, category_id);
            CREATE TABLE IF NOT EXISTS guidance_versions (
                version_id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL REFERENCES guidance_categories(category_id) ON DELETE CASCADE,
                draft_id TEXT REFERENCES guidance_drafts(draft_id) ON DELETE SET NULL,
                version INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(category_id, version)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_guidance_versions_current
                ON guidance_versions(category_id) WHERE is_current = 1;
            CREATE TABLE IF NOT EXISTS guidance_changes (
                change_id TEXT PRIMARY KEY,
                base_snapshot_id TEXT NOT NULL,
                head_snapshot_id TEXT NOT NULL,
                update_level TEXT NOT NULL,
                changed_files_json TEXT NOT NULL,
                affected_categories_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                processed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_guidance_changes_pending
                ON guidance_changes(processed_at, created_at);
            """
        )
        try:
            self.connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(id UNINDEXED, title, content, tags)"
            )
            self.set_meta("fts", "enabled")
        except sqlite3.OperationalError:
            self.set_meta("fts", "disabled")
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        self.connection.commit()

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

    def replace_file(self, indexed: IndexedFile, parsed: ParseResult) -> None:
        self.connection.execute("DELETE FROM files WHERE path = ?", (indexed.path,))
        self.connection.execute(
            "INSERT INTO files(path, language, module, size, mtime_ns, hash, parser, parse_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (indexed.path, indexed.language, indexed.module, indexed.size, indexed.mtime_ns, indexed.content_hash, parsed.parser, parsed.parse_error),
        )
        self.connection.executemany(
            "INSERT INTO symbols(id, name, kind, path, line, end_line, signature, hash, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(item.id, item.name, item.kind, item.path, item.line, item.end_line, item.signature, item.source_hash, item.confidence) for item in parsed.symbols],
        )
        self.connection.executemany(
            "INSERT INTO relations(source, target, kind, path, line, confidence, resolved) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(item.source, item.target, item.kind, item.path, item.line, item.confidence, int(item.resolved)) for item in parsed.relations],
        )
        self.connection.executemany(
            "INSERT INTO routes(method, route, handler, path, line) VALUES (?, ?, ?, ?, ?)",
            [(item.method, item.route, item.handler, item.path, item.line) for item in parsed.routes],
        )

    def delete_files(self, paths: Iterable[str]) -> None:
        self.connection.executemany("DELETE FROM files WHERE path = ?", [(path,) for path in paths])

    def resolve_relations(self) -> None:
        symbols = list(self.connection.execute("SELECT id, name FROM symbols"))
        by_name: dict[str, list[str]] = {}
        for symbol in symbols:
            by_name.setdefault(symbol["name"], []).append(symbol["id"])
        relations = list(self.connection.execute("SELECT id, target FROM relations WHERE resolved = 0"))
        for relation in relations:
            target_name = relation["target"].rsplit(".", 1)[-1]
            candidates = by_name.get(target_name, [])
            if len(candidates) == 1:
                self.connection.execute(
                    "UPDATE relations SET target = ?, resolved = 1, confidence = MIN(confidence, 0.9) WHERE id = ?",
                    (candidates[0], relation["id"]),
                )

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
        tables = ["files", "symbols", "relations", "routes", "knowledge"]
        counts = {table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}
        counts["modules"] = int(self.connection.execute("SELECT COUNT(DISTINCT module) FROM files").fetchone()[0])
        counts["parse_errors"] = int(self.connection.execute("SELECT COUNT(*) FROM files WHERE parse_error IS NOT NULL").fetchone()[0])
        counts["unresolved_relations"] = int(self.connection.execute("SELECT COUNT(*) FROM relations WHERE resolved = 0").fetchone()[0])
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
