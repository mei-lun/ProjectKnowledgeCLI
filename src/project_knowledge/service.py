from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .engine import CodeIndexEngine, IndexedFile, create_engine
from .knowledge import KnowledgeGenerator
from .models import ChangeSet
from .schemas import all_schemas
from .store import KnowledgeStore
from .util import atomic_json, atomic_write, git_root, git_status, marker_update, project_lock, run_git, utc_now


AGENTS_BODY = """Use the local Project Knowledge System before broad repository exploration.

1. Call `knowledge_status` at task start.
2. Call `knowledge_context` for the user task and `knowledge_impact` before cross-module changes.
3. Treat `verified` and `generated` facts according to their reported freshness; verify stale or inferred claims in live source.
4. Read only the source files needed to confirm and implement the change.
5. Run the returned verification commands or the repository's documented checks.
6. Report whether generated knowledge was synchronized and whether curated knowledge needs review.
"""

GITIGNORE_BODY = """.project-kb/index.db
.project-kb/index.db-*
.project-kb/state.json
.project-kb/write.lock
.project-kb/events/
.project-kb/logs/
"""


class ProjectService:
    def __init__(self, path: str | Path = "."):
        candidate = Path(path).resolve()
        self.root = candidate if (candidate / ".project-kb.yml").exists() else git_root(candidate)
        self.config = ProjectConfig.load(self.root)
        self.engine: CodeIndexEngine = create_engine(self.config)
        self.db_path = self.root / ".project-kb" / "index.db"

    def initialize(self, dry_run: bool = False) -> dict[str, Any]:
        discovered = self.engine.discover(self.root, self.config)
        if dry_run:
            return {
                "action": "init",
                "dry_run": True,
                "project_root": str(self.root),
                "files_to_index": len(discovered),
                "files_to_create": [".project-kb.yml", ".project-kb/manifest.json", "docs/knowledge/index.md", "AGENTS.md"],
            }
        with project_lock(self.root):
            self._prepare_project()
            self.config = ProjectConfig.load(self.root)
            self.engine = create_engine(self.config)
            discovered = self.engine.discover(self.root, self.config)
            result = self._atomic_rebuild(discovered)
        result["action"] = "init"
        return result

    def rebuild(self, dry_run: bool = False) -> dict[str, Any]:
        self._require_initialized()
        discovered = self.engine.discover(self.root, self.config)
        if dry_run:
            return {"action": "rebuild", "dry_run": True, "files_to_index": len(discovered)}
        with project_lock(self.root):
            result = self._atomic_rebuild(discovered)
        result["action"] = "rebuild"
        return result

    def _prepare_project(self) -> None:
        self.config = ProjectConfig.load(self.root)
        if not (self.root / ".project-kb.yml").exists():
            self.config = ProjectConfig(project_name=self.root.name)
            self.config.write(self.root)
        for directory in ["events", "proposals", "logs"]:
            (self.root / ".project-kb" / directory).mkdir(parents=True, exist_ok=True)
        for name, schema in all_schemas().items():
            atomic_json(self.root / ".project-kb" / "schemas" / name, schema)
        marker_update(self.root / ".gitignore", "gitignore", GITIGNORE_BODY)
        marker_update(self.root / "AGENTS.md", "instructions", AGENTS_BODY)
        self._write_mcp_config()

    def _atomic_rebuild(self, discovered: list[IndexedFile]) -> dict[str, Any]:
        started = time.monotonic()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        preserved_knowledge = []
        if self.db_path.exists():
            with KnowledgeStore(self.db_path, readonly=True) as current_store:
                preserved_knowledge = [
                    record for record in current_store.all_knowledge()
                    if record.ownership in {"curated", "decision"}
                ]
        descriptor, temporary_name = tempfile.mkstemp(prefix="index.", suffix=".db", dir=self.db_path.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with KnowledgeStore(temporary) as store:
                store.initialize()
                with store.transaction():
                    for record in preserved_knowledge:
                        store.upsert_knowledge(record)
                    for item in discovered:
                        store.replace_file(item, self.engine.parse(self.root, item))
                    store.resolve_relations()
                    self._update_metadata(store, full=True, duration_ms=0)
                    records = KnowledgeGenerator(self.root, self.config, store).generate()
                store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                counts = store.counts()
            os.replace(temporary, self.db_path)
            Path(str(self.db_path) + "-wal").unlink(missing_ok=True)
            Path(str(self.db_path) + "-shm").unlink(missing_ok=True)
            for suffix in ["-wal", "-shm"]:
                Path(str(temporary) + suffix).unlink(missing_ok=True)
            duration = int((time.monotonic() - started) * 1000)
            with KnowledgeStore(self.db_path) as store:
                store.set_meta("last_full_index_duration_ms", str(duration))
                store.connection.commit()
            self._write_state("idle")
            return self._report(counts, discovered, len(records), duration)
        finally:
            temporary.unlink(missing_ok=True)
            Path(str(temporary) + "-wal").unlink(missing_ok=True)
            Path(str(temporary) + "-shm").unlink(missing_ok=True)

    def sync(self, dry_run: bool = False, task_summary: str = "manual synchronization") -> dict[str, Any]:
        self._require_initialized()
        started = time.monotonic()
        discovered = self.engine.discover(self.root, self.config)
        by_path = {item.path: item for item in discovered}
        with KnowledgeStore(self.db_path, readonly=True) as readonly:
            previous = readonly.file_hashes()
            base_commit = readonly.get_meta("head_commit")
            knowledge_changed = self._pending_knowledge(readonly)
        changed = sorted(path for path, item in by_path.items() if previous.get(path) != item.content_hash)
        deleted = sorted(set(previous) - set(by_path))
        if dry_run:
            return {
                "action": "sync", "dry_run": True, "changed_files": changed,
                "deleted_files": deleted, "changed_knowledge": knowledge_changed,
            }
        if not changed and not deleted and not knowledge_changed:
            return {"action": "sync", "changed_files": [], "deleted_files": [], "message": "index is current"}
        with project_lock(self.root), KnowledgeStore(self.db_path) as store:
            with store.transaction():
                store.delete_files(deleted)
                changed_symbols: list[str] = []
                for path in changed:
                    parsed = self.engine.parse(self.root, by_path[path])
                    changed_symbols.extend(symbol.id for symbol in parsed.symbols)
                    store.replace_file(by_path[path], parsed)
                store.resolve_relations()
                duration = int((time.monotonic() - started) * 1000)
                self._update_metadata(store, full=False, duration_ms=duration)
                records = KnowledgeGenerator(self.root, self.config, store).generate(refresh_generated=bool(changed or deleted))
                affected_modules = sorted({by_path[path].module for path in changed if path in by_path})
                affected_knowledge = sorted(
                    record.id for record in records
                    if any((source.path in changed or source.path in deleted or source.id in changed_symbols) for source in record.sources)
                )
            changeset = ChangeSet(
                id=f"change-{time.strftime('%Y%m%d-%H%M%S')}", base_commit=base_commit,
                head_commit=run_git(self.root, "rev-parse", "HEAD"), task_summary=task_summary,
                changed_files=[*changed, *deleted], changed_symbols=changed_symbols,
                affected_modules=affected_modules, affected_knowledge=affected_knowledge,
            )
            atomic_json(self.root / ".project-kb" / "events" / f"{changeset.id}.json", changeset.to_dict())
            counts = store.counts()
        self._write_state("idle")
        return {
            "action": "sync", "changed_files": changed, "deleted_files": deleted, "changed_knowledge": knowledge_changed,
            "affected_modules": affected_modules, "affected_knowledge": affected_knowledge,
            "duration_ms": duration, "counts": counts, "changeset": changeset.id,
        }

    def status(self) -> dict[str, Any]:
        initialized = self.db_path.exists() and (self.root / ".project-kb.yml").exists()
        if not initialized:
            return {"initialized": False, "project_root": str(self.root)}
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as executor:
            discovery_future = executor.submit(self.engine.discover, self.root, self.config)
            git_future = executor.submit(git_status, self.root)
            with KnowledgeStore(self.db_path, readonly=True) as store:
                previous = store.file_hashes()
                knowledge_pending = self._pending_knowledge(store)
                counts = store.counts()
                metadata = store.metadata()
                language_rows = store.rows("SELECT language, COUNT(*) AS count FROM files GROUP BY language ORDER BY count DESC")
                errors = store.rows("SELECT path, parse_error FROM files WHERE parse_error IS NOT NULL ORDER BY path LIMIT 50")
                query = store.rows("SELECT COUNT(*) AS count, COALESCE(AVG(output_tokens), 0) AS avg_tokens FROM query_stats")
            discovered = discovery_future.result()
            git = git_future.result()
        current = {item.path: item.content_hash for item in discovered}
        pending = sorted(path for path, value in current.items() if previous.get(path) != value)
        pending.extend(sorted(set(previous) - set(current)))
        pending.extend(path for path in knowledge_pending if path not in pending)
        state_path = self.root / ".project-kb" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"watcher": "stopped"}
        proposals = len(list((self.root / ".project-kb" / "proposals").glob("*.json")))
        return {
            "initialized": True,
            "project_root": str(self.root),
            "branch": git["branch"],
            "head_commit": git["head_commit"],
            "index_commit": metadata.get("head_commit"),
            "working_tree": "dirty" if git["dirty"] else "clean",
            "pending_files": pending,
            "counts": counts,
            "languages": language_rows,
            "parse_errors": errors,
            "pending_proposals": proposals,
            "database_bytes": self.db_path.stat().st_size,
            "watcher": state.get("watcher", "stopped"),
            "last_full_index_at": metadata.get("last_full_index_at"),
            "last_sync_at": metadata.get("last_sync_at"),
            "last_full_index_duration_ms": int(metadata.get("last_full_index_duration_ms", "0")),
            "last_sync_duration_ms": int(metadata.get("last_sync_duration_ms", "0")),
            "query_stats": query[0] if query else {"count": 0, "avg_tokens": 0},
            "status_duration_ms": int((time.monotonic() - started) * 1000),
            "engine": self.engine.status(),
        }

    def check(self) -> tuple[dict[str, Any], bool]:
        status = self.status()
        healthy = bool(status.get("initialized")) and not status.get("pending_files")
        if healthy:
            counts = status.get("counts", {})
            healthy = counts.get("stale_knowledge", 0) == 0 and counts.get("conflicted_knowledge", 0) == 0
        return status, healthy

    def watch(self, once: bool = False) -> None:
        self._require_initialized()
        self._write_state("running", pid=os.getpid())
        try:
            while True:
                result = self.sync(task_summary="watcher synchronization")
                if once:
                    return
                time.sleep(max(0.1, self.config.debounce_ms / 1000))
        finally:
            self._write_state("stopped")

    def install(self, dry_run: bool = False) -> dict[str, Any]:
        targets = [self.root / "AGENTS.md"]
        if dry_run:
            return {"action": "install", "dry_run": True, "targets": [str(path) for path in targets]}
        changed = marker_update(targets[0], "instructions", AGENTS_BODY)
        self._write_mcp_config()
        return {"action": "install", "agents_updated": changed, "mcp_config": ".project-kb/mcp.json"}

    def uninstall(self, dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            return {"action": "uninstall", "dry_run": True, "targets": ["AGENTS.md", ".project-kb/mcp.json"], "knowledge_preserved": True}
        changed = marker_update(self.root / "AGENTS.md", "instructions", None)
        mcp_path = self.root / ".project-kb" / "mcp.json"
        mcp_removed = mcp_path.exists()
        mcp_path.unlink(missing_ok=True)
        return {"action": "uninstall", "agents_updated": changed, "mcp_removed": mcp_removed, "knowledge_preserved": True}

    def doctor(self) -> dict[str, Any]:
        return {
            "python": os.sys.version.split()[0],
            "sqlite": __import__("sqlite3").sqlite_version,
            "git": shutil.which("git"),
            "project_root": str(self.root),
            "config": (self.root / ".project-kb.yml").exists(),
            "database": self.db_path.exists(),
            "writable": os.access(self.root, os.W_OK),
            "engine": self.engine.status(),
        }

    def _update_metadata(self, store: KnowledgeStore, full: bool, duration_ms: int) -> None:
        now = utc_now()
        store.set_meta("head_commit", run_git(self.root, "rev-parse", "HEAD") or "")
        store.set_meta("branch", run_git(self.root, "branch", "--show-current") or "")
        store.set_meta("worktree", run_git(self.root, "rev-parse", "--show-toplevel") or str(self.root))
        store.set_meta("last_sync_at", now)
        store.set_meta("last_sync_duration_ms", str(duration_ms))
        if full:
            store.set_meta("last_full_index_at", now)

    def _report(self, counts: dict[str, int], files: list[IndexedFile], pages: int, duration_ms: int) -> dict[str, Any]:
        languages = Counter(item.language for item in files)
        recommendations = ["Review unresolved and low-confidence relations before high-risk changes."]
        architecture = self.root / self.config.curated_root / "architecture.md"
        if not architecture.exists() or "Document module responsibilities" in architecture.read_text(encoding="utf-8", errors="replace"):
            recommendations.insert(0, "Complete docs/knowledge/curated/architecture.md with verified boundaries.")
        return {
            "project_root": str(self.root), "files_scanned": len(files), "parse_success_rate": round(
                (len(files) - counts["parse_errors"]) / max(1, len(files)), 4
            ), "languages": dict(languages), "symbols": counts["symbols"], "relations": counts["relations"],
            "modules": counts["modules"], "knowledge_pages": pages, "unresolved_relations": counts["unresolved_relations"],
            "parse_errors": counts["parse_errors"], "duration_ms": duration_ms,
            "excluded_paths": self.config.exclude,
            "review_recommendations": recommendations,
        }

    def _write_state(self, watcher: str, pid: int | None = None) -> None:
        atomic_json(self.root / ".project-kb" / "state.json", {
            "watcher": watcher, "pid": pid, "updated_at": utc_now(),
            "head_commit": run_git(self.root, "rev-parse", "HEAD"),
        })

    def _write_mcp_config(self) -> None:
        atomic_json(self.root / ".project-kb" / "mcp.json", {
            "mcpServers": {
                "project-knowledge": {
                    "command": "project-kb",
                    "args": ["mcp", "--project", "."],
                }
            }
        })

    def _pending_knowledge(self, store: KnowledgeStore) -> list[str]:
        indexed = {
            row["path"]: row["content"]
            for row in store.connection.execute("SELECT path, content FROM knowledge WHERE ownership IN ('curated', 'decision')")
        }
        current: dict[str, str] = {}
        for base_name in [self.config.curated_root, self.config.decisions_root]:
            base = self.root / base_name
            if not base.exists():
                continue
            for path in base.rglob("*.md"):
                relative = path.relative_to(self.root).as_posix()
                current[relative] = path.read_text(encoding="utf-8", errors="replace")
        changed = [path for path, content in current.items() if indexed.get(path) != content]
        changed.extend(path for path in indexed if path not in current)
        return sorted(set(changed))

    def _require_initialized(self) -> None:
        if not self.db_path.exists() or not (self.root / ".project-kb.yml").exists():
            raise RuntimeError(f"{self.root} is not initialized; run project-kb init first")
