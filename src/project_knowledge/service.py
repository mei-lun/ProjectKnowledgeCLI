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
from .guidance import GuidanceService
from .knowledge import KnowledgeGenerator
from .models import ChangeSet
from .proposal import ProposalService
from .schemas import CHANGE_SET_SCHEMA, all_schemas, validate_instance
from .store import KnowledgeStore
from .util import (
    append_jsonl,
    atomic_json,
    atomic_write,
    git_root,
    git_status,
    hash_file,
    marker_update,
    process_alive,
    project_lock,
    run_git,
    script_marker_update,
    utc_now,
    watcher_lock,
)


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
.project-kb/watcher.lock
.project-kb/events/
.project-kb/logs/
"""

HOOK_BODY = """# project-kb lifecycle hook
project-kb sync "$PWD" --quiet >/dev/null 2>&1 || true
"""

CLIENT_BODIES = {
    "claude": ("CLAUDE.md", "请先读取项目知识库状态、任务上下文和影响分析，再进行跨模块修改。"),
    "cursor": ("project-knowledge.mdc", "description: Project Knowledge System context\n请使用本地知识库并执行返回的验证命令。"),
    "gemini": ("GEMINI.md", "默认使用中文知识文档；修改前读取项目知识库上下文和影响范围。"),
}


class ProjectService:
    def __init__(self, path: str | Path = "."):
        candidate = Path(path).resolve()
        self.root = candidate if (candidate / ".project-kb.yml").exists() else git_root(candidate)
        self.config = ProjectConfig.load(self.root)
        self.engine: CodeIndexEngine = create_engine(self.config)
        self.db_path = self.root / ".project-kb" / "index.db"
        self._watching = False
        self._watch_lease: dict[str, Any] | None = None

    def initialize(self, dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            if self.config.engine == "codegraph":
                from .engine import BuiltinCodeIndexEngine
                discovered = BuiltinCodeIndexEngine().discover(self.root, self.config)
            else:
                discovered = self.engine.discover(self.root, self.config)
            return {
                "action": "init",
                "dry_run": True,
                "project_root": str(self.root),
                "files_to_index": len(discovered),
                "files_to_create": [
                    ".project-kb.yml", ".project-kb/manifest.json",
                    ".project-kb/generated/项目地图.md", ".project-kb/generated/开发指导索引.md",
                ],
            }
        with project_lock(self.root):
            self._prepare_project()
            self.config = ProjectConfig.load(self.root)
            self.engine = create_engine(self.config)
            codegraph = self.engine.initialize(self.root, self.config) if self.config.engine == "codegraph" else None
            discovered = self.engine.discover(self.root, self.config)
            result = self._atomic_rebuild(discovered)
        result["action"] = "init"
        if codegraph is not None:
            result["codegraph"] = codegraph
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
        for directory in ["events", "proposals", "proposals/queue", "logs", "state", "codegraph", "evidence", "methodology", "guides", "generated", "drafts", "curated", "decisions"]:
            (self.root / ".project-kb" / directory).mkdir(parents=True, exist_ok=True)
        for name, schema in all_schemas().items():
            atomic_json(self.root / ".project-kb" / "schemas" / name, schema)
        marker_update(self.root / ".gitignore", "gitignore", GITIGNORE_BODY)
        self._write_mcp_config()

    def _atomic_rebuild(self, discovered: list[IndexedFile]) -> dict[str, Any]:
        started = time.monotonic()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        preserved_knowledge = []
        preserved_guidance: dict[str, list[dict[str, Any]]] = {}
        if self.db_path.exists():
            with KnowledgeStore(self.db_path) as current_store:
                current_store.initialize()
                preserved_knowledge = [
                    record for record in current_store.all_knowledge()
                    if record.ownership in {"draft", "curated", "decision"}
                ]
                preserved_guidance = current_store.export_guidance_graph()
        descriptor, temporary_name = tempfile.mkstemp(prefix="index.", suffix=".db", dir=self.db_path.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with KnowledgeStore(temporary) as store:
                store.initialize()
                with store.transaction():
                    for record in preserved_knowledge:
                        store.upsert_knowledge(record)
                    store.import_guidance_graph(preserved_guidance)
                    for item in discovered:
                        parsed, stable_item = self._parse_stable(item)
                        store.replace_file(stable_item, parsed)
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
            report = self._report(counts, discovered, len(records), duration)
            guidance = self._refresh_guidance()
            if guidance is not None:
                report["guidance"] = guidance
            return report
        finally:
            temporary.unlink(missing_ok=True)
            Path(str(temporary) + "-wal").unlink(missing_ok=True)
            Path(str(temporary) + "-shm").unlink(missing_ok=True)

    def sync(self, dry_run: bool = False, task_summary: str = "manual synchronization") -> dict[str, Any]:
        self._require_initialized()
        started = time.monotonic()
        codegraph_sync = None
        if self.config.engine == "codegraph" and not dry_run:
            codegraph_sync = self.engine.sync(self.root, self.config)
        discovered = self.engine.discover(self.root, self.config)
        by_path = {item.path: item for item in discovered}
        with KnowledgeStore(self.db_path, readonly=True) as readonly:
            previous = readonly.file_hashes()
            base_commit = readonly.get_meta("head_commit") or None
            base_branch = readonly.get_meta("branch") or None
            knowledge_changed = self._pending_knowledge(readonly)
        git = git_status(self.root)
        current_commit = git["head_commit"] or None
        current_branch = git["branch"] or None
        branch_changed = base_branch != current_branch
        commit_changed = base_commit != current_commit or branch_changed
        changed = sorted(path for path, item in by_path.items() if previous.get(path) != item.content_hash)
        deleted = sorted(set(previous) - set(by_path))
        if dry_run:
            return {
                "action": "sync", "dry_run": True, "changed_files": changed,
                "deleted_files": deleted, "changed_knowledge": knowledge_changed,
                "commit_reconciliation_required": commit_changed,
                "branch_reconciliation_required": branch_changed,
            }
        if not changed and not deleted and not knowledge_changed and not commit_changed:
            return {"action": "sync", "changed_files": [], "deleted_files": [], "message": "index is current"}
        with project_lock(self.root), KnowledgeStore(self.db_path) as store:
            with store.transaction():
                store.delete_files(deleted)
                changed_symbols: list[str] = []
                for path in changed:
                    parsed, stable_item = self._parse_stable(by_path[path])
                    changed_symbols.extend(symbol.id for symbol in parsed.symbols)
                    by_path[path] = stable_item
                    store.replace_file(stable_item, parsed)
                store.resolve_relations()
                duration = int((time.monotonic() - started) * 1000)
                self._update_metadata(store, full=False, duration_ms=duration)
                records = KnowledgeGenerator(self.root, self.config, store).generate(
                    refresh_generated=bool(changed or deleted or commit_changed)
                )
                affected_modules = sorted({by_path[path].module for path in changed if path in by_path})
                affected_knowledge = sorted(
                    record.id for record in records
                    if any((source.path in changed or source.path in deleted or source.id in changed_symbols) for source in record.sources)
                )
            changeset = ChangeSet(
                id=f"change-{time.strftime('%Y%m%d-%H%M%S')}", base_commit=base_commit,
                head_commit=current_commit, task_summary=task_summary,
                changed_files=[*changed, *deleted], changed_symbols=changed_symbols,
                affected_modules=affected_modules, affected_knowledge=affected_knowledge,
            )
            changeset_payload = changeset.to_dict()
            validate_instance(changeset_payload, CHANGE_SET_SCHEMA)
            atomic_json(self.root / ".project-kb" / "events" / f"{changeset.id}.json", changeset_payload)
            counts = store.counts()
        semantic_paths = [
            path for path in [*changed, *deleted]
            if not path.startswith((
                ".github/", "docs/", "evaluation/", "tests/", ".project-kb/",
            ))
        ]
        queue_item = (
            ProposalService(self.root).enqueue_changeset(changeset_payload)
            if semantic_paths else None
        )
        if self._watching:
            self._write_state("running", pid=os.getpid(), coordinator=self._watch_lease)
        else:
            self._write_state("idle")
        guidance = self._refresh_guidance()
        return {
            "action": "sync", "changed_files": changed, "deleted_files": deleted, "changed_knowledge": knowledge_changed,
            "affected_modules": affected_modules, "affected_knowledge": affected_knowledge,
            "duration_ms": duration, "counts": counts, "changeset": changeset.id,
            "semantic_update": queue_item["queue_id"] if queue_item else None,
            "commit_reconciled": commit_changed,
            "branch_reconciled": branch_changed,
            "codegraph": codegraph_sync,
            "guidance": guidance,
        }

    def _refresh_guidance(self) -> dict[str, Any] | None:
        if self.config.engine != "codegraph" or self.config.project_name.lower() != "gardenserver":
            return None
        try:
            client = getattr(self.engine, "client", None)
            result = GuidanceService(self.root, client=client).generate()
            return {"status": result["status"], "categories": result["categories"], "generated_at": result["generated_at"]}
        except (OSError, RuntimeError, ValueError) as error:
            self._log_event("guidance_refresh_failed", error=str(error))
            return {"status": "stale", "error": str(error)}

    def _parse_stable(self, item: IndexedFile, attempts: int = 3):
        current = item
        for _ in range(max(1, attempts)):
            try:
                before = hash_file(self.root / current.path)
            except OSError as error:
                raise RuntimeError(f"source disappeared while indexing: {current.path}") from error
            parsed = self.engine.parse(self.root, current)
            try:
                after = hash_file(self.root / current.path)
            except OSError as error:
                raise RuntimeError(f"source disappeared while indexing: {current.path}") from error
            if before == after:
                if before == current.content_hash:
                    return parsed, current
                refreshed = next(
                    (candidate for candidate in self.engine.discover(self.root, self.config)
                     if candidate.path == current.path),
                    None,
                )
                if refreshed is not None and refreshed.content_hash == after:
                    return parsed, refreshed
                if refreshed is None:
                    raise RuntimeError(f"source disappeared while indexing: {current.path}")
                current = refreshed
                continue
            refreshed = next(
                (candidate for candidate in self.engine.discover(self.root, self.config)
                 if candidate.path == current.path),
                None,
            )
            if refreshed is None:
                raise RuntimeError(f"source disappeared while indexing: {current.path}")
            current = refreshed
        raise RuntimeError(f"source changed during indexing; retry required: {item.path}")

    def _watcher_health(self, state: dict[str, Any]) -> dict[str, Any]:
        pid = state.get("pid")
        try:
            numeric_pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            numeric_pid = None
        alive = process_alive(numeric_pid) if state.get("watcher") == "running" else False
        return {
            "pid": numeric_pid,
            "alive": alive,
            "stale": state.get("watcher") == "running" and not alive,
            "last_heartbeat": state.get("heartbeat") or state.get("updated_at"),
            "error": state.get("error"),
            "coordinator": state.get("coordinator"),
        }

    def _log_event(self, event: str, **fields: Any) -> None:
        append_jsonl(self.root / ".project-kb" / "logs" / "service.jsonl", {
            "event": event,
            "timestamp": utc_now(),
            "pid": os.getpid(),
            **fields,
        })

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
        proposal_service = ProposalService(self.root)
        proposals = proposal_service.pending_count()
        semantic_updates = sum(
            1 for item in proposal_service.queue_items()
            if item.get("status") == "awaiting_semantic_generation"
        )
        head_commit = git["head_commit"] or None
        index_commit = metadata.get("head_commit") or None
        branch_aligned = (git["branch"] or None) == (metadata.get("branch") or None)
        content_fresh = not pending
        commits_since_index = self._commits_since_index(index_commit, head_commit)
        commit_aligned = head_commit == index_commit and branch_aligned
        generated_outputs_only = (
            branch_aligned
            and content_fresh
            and index_commit is not None
            and head_commit is not None
            and head_commit != index_commit
            and commits_since_index is not None
            and bool(commits_since_index)
            and all(self._is_generated_output(path) for path in commits_since_index)
        )
        verification_aligned = commit_aligned or generated_outputs_only
        watcher_health = self._watcher_health(state)
        watcher_state = state.get("watcher", "stopped")
        if watcher_state == "running" and watcher_health["stale"]:
            watcher_state = "crashed"
        return {
            "initialized": True,
            "project_root": str(self.root),
            "branch": git["branch"],
            "head_commit": head_commit,
            "index_commit": index_commit,
            "content_fresh": content_fresh,
            "branch_aligned": branch_aligned,
            "commit_aligned": commit_aligned,
            "verification_aligned": verification_aligned,
            "commits_since_index": commits_since_index or [],
            "commit_alignment": (
                "aligned" if commit_aligned
                else "branch_changed" if not branch_aligned
                else "generated_outputs_only" if generated_outputs_only
                else "content_unvalidated_at_head"
            ),
            "working_tree": "dirty" if git["dirty"] else "clean",
            "pending_files": pending,
            "counts": counts,
            "languages": language_rows,
            "parse_errors": errors,
            "pending_proposals": proposals,
            "semantic_update_queue": semantic_updates,
            "database_bytes": self.db_path.stat().st_size,
            "watcher": watcher_state,
            "watcher_health": watcher_health,
            "last_full_index_at": metadata.get("last_full_index_at"),
            "last_sync_at": metadata.get("last_sync_at"),
            "last_full_index_duration_ms": int(metadata.get("last_full_index_duration_ms", "0")),
            "last_sync_duration_ms": int(metadata.get("last_sync_duration_ms", "0")),
            "query_stats": query[0] if query else {"count": 0, "avg_tokens": 0},
            "status_duration_ms": int((time.monotonic() - started) * 1000),
            "engine": self.engine.status(),
            "configuration_warnings": self.config.capability_warnings(),
        }

    def check(self) -> tuple[dict[str, Any], bool]:
        status = self.status()
        healthy = bool(status.get("initialized")) and bool(status.get("content_fresh")) and bool(status.get("verification_aligned"))
        if healthy:
            counts = status.get("counts", {})
            healthy = counts.get("stale_knowledge", 0) == 0 and counts.get("conflicted_knowledge", 0) == 0
        if healthy:
            healthy = not bool(status.get("watcher_health", {}).get("stale"))
        return status, healthy

    def migrate(self, dry_run: bool = False) -> dict[str, Any]:
        result = ProjectConfig.migrate_file(self.root, dry_run=dry_run)
        if not dry_run and result.get("changed"):
            self.config = ProjectConfig.load(self.root)
        return result

    def _client_targets(self) -> dict[str, tuple[Path, str]]:
        return {
            "claude": (self.root / ".claude" / CLIENT_BODIES["claude"][0], "claude"),
            "cursor": (self.root / ".cursor" / "rules" / CLIENT_BODIES["cursor"][0], "cursor"),
            "gemini": (self.root / CLIENT_BODIES["gemini"][0], "gemini"),
        }

    def watch(self, once: bool = False) -> None:
        self._require_initialized()
        with watcher_lock(self.root) as lease:
            self._watching = True
            self._watch_lease = lease
            self._write_state("running", pid=os.getpid(), coordinator=lease)
            self._log_event("watch_started", once=once, coordinator=lease)
            try:
                while True:
                    self.sync(task_summary="watcher synchronization")
                    self._write_state("running", pid=os.getpid(), coordinator=lease)
                    if once:
                        return
                    time.sleep(max(0.1, self.config.debounce_ms / 1000))
            except BaseException as error:
                self._write_state("error", pid=os.getpid(), coordinator=lease, error=str(error))
                self._log_event("watch_error", error=str(error))
                raise
            finally:
                self._watching = False
                self._watch_lease = None
                self._write_state("stopped")
                self._log_event("watch_stopped", once=once)

    def install(self, dry_run: bool = False, clients: list[str] | None = None) -> dict[str, Any]:
        targets = [self.root / "AGENTS.md"]
        selected = sorted(set(clients or self._client_targets()))
        unknown = sorted(set(selected) - set(self._client_targets()))
        if unknown:
            raise ValueError("不支持的客户端：" + "、".join(unknown))
        hook_names = ["post-checkout", "post-merge", "pre-commit"]
        git_hooks = self.root / ".git" / "hooks"
        if dry_run:
            return {
                "action": "install", "dry_run": True,
                "targets": [str(path) for path in targets],
                "hooks": [str(git_hooks / name) for name in hook_names] if git_hooks.exists() else [],
                "clients": selected,
                "client_targets": [str(self._client_targets()[name][0]) for name in selected],
            }
        changed = marker_update(targets[0], "instructions", AGENTS_BODY)
        self._write_mcp_config()
        hooks: list[str] = []
        if git_hooks.exists():
            for name in hook_names:
                path = git_hooks / name
                script_marker_update(path, "hook", HOOK_BODY)
                path.chmod(path.stat().st_mode | 0o111)
                hooks.append(str(path))
        installed_clients: list[str] = []
        for name in selected:
            path, marker = self._client_targets()[name]
            body = CLIENT_BODIES[name][1]
            marker_update(path, marker, body)
            installed_clients.append(name)
        return {
            "action": "install", "agents_updated": changed, "mcp_config": ".project-kb/mcp.json",
            "hooks": hooks, "clients": installed_clients,
        }

    def uninstall(self, dry_run: bool = False, clients: list[str] | None = None) -> dict[str, Any]:
        selected = sorted(set(clients or self._client_targets()))
        unknown = sorted(set(selected) - set(self._client_targets()))
        if unknown:
            raise ValueError("不支持的客户端：" + "、".join(unknown))
        hook_names = ["post-checkout", "post-merge", "pre-commit"]
        git_hooks = self.root / ".git" / "hooks"
        if dry_run:
            return {
                "action": "uninstall", "dry_run": True,
                "targets": ["AGENTS.md", ".project-kb/mcp.json"],
                "hooks": [str(git_hooks / name) for name in hook_names] if git_hooks.exists() else [],
                "clients": selected,
                "knowledge_preserved": True,
            }
        changed = marker_update(self.root / "AGENTS.md", "instructions", None)
        mcp_path = self.root / ".project-kb" / "mcp.json"
        mcp_removed = mcp_path.exists()
        mcp_path.unlink(missing_ok=True)
        hooks_removed: list[str] = []
        if git_hooks.exists():
            for name in hook_names:
                path = git_hooks / name
                if script_marker_update(path, "hook", None):
                    hooks_removed.append(str(path))
        clients_removed: list[str] = []
        for name in selected:
            path, marker = self._client_targets()[name]
            if marker_update(path, marker, None):
                clients_removed.append(name)
        return {
            "action": "uninstall", "agents_updated": changed, "mcp_removed": mcp_removed,
            "hooks_removed": hooks_removed, "clients_removed": clients_removed,
            "knowledge_preserved": True,
        }

    def doctor(self) -> dict[str, Any]:
        status = self.status() if self.db_path.exists() else {}
        return {
            "python": os.sys.version.split()[0],
            "sqlite": __import__("sqlite3").sqlite_version,
            "git": shutil.which("git"),
            "project_root": str(self.root),
            "config": (self.root / ".project-kb.yml").exists(),
            "database": self.db_path.exists(),
            "writable": os.access(self.root, os.W_OK),
            "engine": self.engine.status(),
            "watcher": status.get("watcher", "stopped"),
            "watcher_health": status.get("watcher_health", {"alive": False, "stale": False}),
            "branch_aligned": status.get("branch_aligned"),
            "commit_aligned": status.get("commit_aligned"),
            "verification_aligned": status.get("verification_aligned"),
            "commit_alignment": status.get("commit_alignment"),
            "package_source": self._package_source_provenance(),
            "configuration_warnings": self.config.capability_warnings(),
        }

    def _package_source_provenance(self) -> dict[str, Any]:
        package_file = Path(__file__).resolve()
        expected_source = (self.root / "src" / "project_knowledge").resolve()
        is_source_checkout = (expected_source / "__init__.py").exists()
        if is_source_checkout:
            try:
                aligned: bool | None = package_file.is_relative_to(expected_source)
            except AttributeError:
                aligned = str(package_file).startswith(str(expected_source))
        else:
            aligned = None
        return {
            "package_file": str(package_file),
            "expected_source": str(expected_source) if is_source_checkout else None,
            "aligned": aligned,
            "scope": "source_checkout" if is_source_checkout else "external_project",
        }

    def _commits_since_index(self, index_commit: str | None, head_commit: str | None) -> list[str] | None:
        if not index_commit or not head_commit or index_commit == head_commit:
            return []
        merge_base = run_git(self.root, "merge-base", index_commit, head_commit)
        if merge_base != index_commit:
            return None
        changed = run_git(self.root, "diff", "--name-only", f"{index_commit}..{head_commit}")
        return changed.splitlines() if changed else []

    def _is_generated_output(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        generated_root = self.config.generated_root.replace("\\", "/").rstrip("/")
        return normalized in {
            ".project-kb/index.md",
            ".project-kb/manifest.json",
            ".project-kb/mcp.json",
        } or normalized.startswith(f"{generated_root}/") or normalized.startswith(
            ".project-kb/generated/"
        ) or normalized.startswith(".project-kb/schemas/") or normalized.startswith(
            ".project-kb/proposals/queue/"
        ) or normalized.startswith("evaluation/reports/") or normalized.startswith(
            "evaluation/baselines/"
        )

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
            recommendations.insert(0, "Complete .project-kb/curated/architecture.md with verified boundaries.")
        return {
            "project_root": str(self.root), "files_scanned": len(files), "parse_success_rate": round(
                (len(files) - counts["parse_errors"]) / max(1, len(files)), 4
            ), "languages": dict(languages), "symbols": counts["symbols"], "relations": counts["relations"],
            "modules": counts["modules"], "knowledge_pages": pages, "unresolved_relations": counts["unresolved_relations"],
            "parse_errors": counts["parse_errors"], "duration_ms": duration_ms,
            "excluded_paths": self.config.exclude,
            "review_recommendations": recommendations,
        }

    def _write_state(
        self,
        watcher: str,
        pid: int | None = None,
        *,
        coordinator: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "watcher": watcher,
            "pid": pid,
            "updated_at": utc_now(),
            "heartbeat": utc_now(),
            "head_commit": run_git(self.root, "rev-parse", "HEAD"),
            "branch": run_git(self.root, "branch", "--show-current"),
        }
        if coordinator is not None:
            payload["coordinator"] = coordinator
        if error:
            payload["error"] = error
        atomic_json(self.root / ".project-kb" / "state.json", payload)

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
            for row in store.connection.execute("SELECT path, content FROM knowledge WHERE ownership IN ('draft', 'curated', 'decision')")
        }
        current: dict[str, str] = {}
        for base_name in [self.config.drafts_root, self.config.curated_root, self.config.decisions_root]:
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
