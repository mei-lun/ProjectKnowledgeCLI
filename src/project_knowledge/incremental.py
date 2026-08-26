from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .codegraph import CodeGraphClient
from .config import ProjectConfig
from .guidance_models import GuidanceChange, GuidanceRun, GuidanceVersion
from .guidance_store import GuidanceStore
from .guidance_workflow import GuidanceWorkflow
from .models import KnowledgeRecord, SourceReference
from .store import KnowledgeStore
from .util import utc_now


class IncrementalWorkflow:
    """Build change-scoped fact packs and apply three update levels."""

    BASELINE_KEY = "guidance_snapshot"

    def __init__(self, project: str | Path, *, client: CodeGraphClient | None = None):
        self.root = Path(project).resolve()
        self.config = ProjectConfig.load(self.root)
        self.client = client or CodeGraphClient(self.root, self.config)
        self.db_path = self.root / ".project-kb" / "index.db"

    def _open(self) -> KnowledgeStore:
        store = KnowledgeStore(self.db_path)
        store.initialize()
        return store

    def changes(self) -> dict[str, Any]:
        current = self.client.snapshot()
        current_files = {item["path"]: item for item in current["files"]}
        with self._open() as store:
            guidance = GuidanceStore(store)
            baseline = self._baseline(store, guidance)
            old_files = dict(baseline.get("files", {}))
            added = sorted(set(current_files) - set(old_files))
            deleted = sorted(set(old_files) - set(current_files))
            modified = sorted(path for path in set(old_files) & set(current_files) if old_files[path] != current_files[path]["content_hash"])
            changed = [*added, *modified, *deleted]
            if not changed:
                return {"status": "current", "change_id": None, "changed_files": [], "affected_categories": [], "facts": [], "next_actions": []}
            base_id = str(baseline.get("snapshot_id", "none"))
            identity = json.dumps([base_id, current["snapshot_id"], sorted(changed)], ensure_ascii=False, separators=(",", ":"))
            change_id = "change-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
            existing = next((item for item in guidance.pending_changes() if item.change_id == change_id), None)
            if existing:
                return {**existing.payload, "status": "pending", "change_id": change_id, "next_actions": ["选择 fact、guidance 或 category 级更新"]}
            facts = []
            affected_paths = set(changed)
            for path in added + modified:
                item = current_files[path]
                source = self.client.source(path, start_line=1, limit=400)
                symbols = item.get("symbols", [])
                impacts: list[dict[str, Any]] = []
                for symbol in symbols[:20]:
                    name = symbol.get("name") if isinstance(symbol, dict) else str(symbol)
                    if not name:
                        continue
                    payload = self.client.impact(str(name), depth=2)
                    candidates = payload.get("affected", []) if isinstance(payload, dict) else []
                    impacts.extend(candidates[:50 - len(impacts)])
                    affected_paths.update(
                        str(candidate.get("filePath", "")).replace("\\", "/")
                        for candidate in candidates if isinstance(candidate, dict) and candidate.get("filePath")
                    )
                    if len(impacts) >= 50:
                        break
                facts.append({
                    "path": path, "change": "added" if path in added else "modified",
                    "hash": item["content_hash"], "language": item["language"],
                    "symbols": symbols, "source": source, "impact": impacts[:50],
                })
            facts.extend({"path": path, "change": "deleted", "old_hash": old_files[path]} for path in deleted)
            affected_categories = []
            for category in guidance.list_categories():
                evidence_paths = {item.get("path") for item in category.evidence if isinstance(item, dict)}
                if evidence_paths & affected_paths or set(category.samples) & affected_paths:
                    affected_categories.append(category.category_id)
            payload = {
                "base_snapshot_id": base_id, "head_snapshot_id": current["snapshot_id"],
                "changed_files": changed, "added": added, "modified": modified, "deleted": deleted,
                "affected_categories": sorted(affected_categories),
                "pendingCategories": sorted(affected_categories),
                "completedCategories": [],
                "categoryLevels": {category_id: "guidance" for category_id in sorted(affected_categories)},
                "facts": facts,
            }
            now = utc_now()
            change = GuidanceChange(
                change_id, str(self.root), base_id, current["snapshot_id"], "guidance",
                changed, sorted(affected_categories), payload, now,
            )
            with store.transaction():
                run_row = store.connection.execute(
                    "SELECT run_id FROM guidance_runs WHERE project_root=? AND snapshot_id=? LIMIT 1",
                    (str(self.root), current["snapshot_id"]),
                ).fetchone()
                if run_row is None:
                    run_id = "run-incr-" + hashlib.sha256(
                        f"{self.root}\0{current['snapshot_id']}".encode()
                    ).hexdigest()[:16]
                    guidance.create_run(GuidanceRun(
                        run_id, str(self.root), current["snapshot_id"],
                        "guidance_generation", len(current_files), len(current_files),
                        now, now,
                    ))
                guidance.save_change(change)
            return {**payload, "status": "pending", "change_id": change_id, "next_actions": ["选择 fact、guidance 或 category 级更新"]}

    def submit_update(self, change_id: str, level: str, *, category_id: str | None = None,
                      content: dict[str, Any] | None = None,
                      evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        content = content or {}
        evidence = evidence or []
        with self._open() as store:
            guidance = GuidanceStore(store)
            change = next((item for item in guidance.pending_changes() if item.change_id == change_id), None)
            if change is None:
                raise KeyError(f"待处理变化不存在：{change_id}")
            if level not in {"fact", "guidance", "category"}:
                raise ValueError(f"更新级别无效：{level}")
            pending_categories = list(change.payload.get("pendingCategories", change.affected_categories))
            if not pending_categories:
                raise ValueError("change has no affected category; classify it before updating")
            selected_category = category_id or pending_categories[0]
            if selected_category != pending_categories[0]:
                raise ValueError("updates must process pending categories in order")
            if level == "fact":
                result = self._submit_fact(store, guidance, change, category_id, content, evidence)
                self._advance(store, guidance, change, selected_category)
                return result
            run_id = self._run_for_snapshot(store, change.head_snapshot_id)
            linked_content = dict(content)
            linked_content["_change_id"] = change_id
            linked_content["_head_snapshot_id"] = change.head_snapshot_id
            linked_content["_snapshot_files"] = {
                item["path"]: item["content_hash"] for item in self.client.snapshot()["files"]
            }
            if level == "guidance":
                if not category_id:
                    raise ValueError("guidance 更新必须指定 categoryId")
                result = GuidanceWorkflow(self.root, client=self.client).save_draft("guidance", run_id, linked_content, category_id)
                result.update({"change_id": change_id, "level": level})
                return result
            result = GuidanceWorkflow(self.root, client=self.client).save_draft("category_catalog", run_id, linked_content)
            result.update({"change_id": change_id, "level": level})
            return result

    def _submit_fact(self, store: KnowledgeStore, guidance: GuidanceStore, change: GuidanceChange,
                     category_id: str | None, content: dict[str, Any],
                     evidence: list[dict[str, Any]]) -> dict[str, Any]:
        if not category_id or not content.get("guidance_unchanged"):
            raise ValueError("fact 更新必须声明 categoryId 和 guidance_unchanged=true")
        current = guidance.current_version(category_id)
        if current is None:
            raise KeyError(f"类别没有正式指导：{category_id}")
        snapshot = self.client.snapshot()
        if snapshot["snapshot_id"] != change.head_snapshot_id:
            raise ValueError("CodeGraph 快照已再次变化")
        hashes = {item["path"]: item["content_hash"] for item in snapshot["files"]}
        for item in evidence:
            path = item.get("path")
            if path not in hashes or item.get("hash") != hashes[path]:
                raise ValueError(f"增量证据无效：{path}")
        number = max(item.version for item in guidance.list_versions(category_id)) + 1
        now = utc_now()
        version = GuidanceVersion(
            f"guide-{category_id}-v{number}", category_id, number, current.title,
            current.content, current.content_hash, change.head_snapshot_id,
            evidence, True, now,
        )
        record = store.get_knowledge(f"guide.{category_id}")
        if record is None:
            raise KeyError(f"正式指导知识不存在：guide.{category_id}")
        record.sources = [SourceReference(type="file", path=item["path"], hash=item["hash"]) for item in evidence]
        record.source_hashes = {item["path"]: item["hash"] for item in evidence}
        record.status = "fresh"
        record.last_verified_at = now
        with store.transaction():
            guidance.save_version(version)
            store.upsert_knowledge(record)
        return {"status": "completed", "change_id": change.change_id, "level": "fact", "version_id": version.version_id, "next_actions": []}

    def _advance(self, store: KnowledgeStore, guidance: GuidanceStore,
                 change: GuidanceChange, category_id: str) -> None:
        now = utc_now()
        with store.transaction():
            payload = dict(change.payload)
            pending = [item for item in payload.get("pendingCategories", change.affected_categories) if item != category_id]
            completed = list(payload.get("completedCategories", []))
            if category_id not in completed:
                completed.append(category_id)
            payload["pendingCategories"] = pending
            payload["completedCategories"] = completed
            change.payload = payload
            guidance.save_change(change)
            if not pending:
                self._advance_baseline(store, guidance, change, now)

    def _advance_baseline(self, store: KnowledgeStore, guidance: GuidanceStore,
                          change: GuidanceChange, now: str) -> None:
        snapshot = self.client.snapshot()
        baseline = {
            "snapshot_id": snapshot["snapshot_id"],
            "files": {item["path"]: item["content_hash"] for item in snapshot["files"]},
        }
        store.set_meta(self.BASELINE_KEY, json.dumps(baseline, ensure_ascii=False, sort_keys=True))
        guidance.mark_change_processed(change.change_id, now)

    def _baseline(self, store: KnowledgeStore, guidance: GuidanceStore) -> dict[str, Any]:
        raw = store.get_meta(self.BASELINE_KEY)
        if raw:
            return json.loads(raw)
        versions = [guidance.current_version(category.category_id) for category in guidance.list_categories()]
        snapshots = [item.snapshot_id for item in versions if item]
        source_hashes: dict[str, str] = {}
        for record in store.all_knowledge():
            if record.kind == "development-guide":
                source_hashes.update(record.source_hashes)
        return {"snapshot_id": snapshots[0] if snapshots else "none", "files": source_hashes}

    def _run_for_snapshot(self, store: KnowledgeStore, snapshot_id: str) -> str:
        row = store.connection.execute(
            "SELECT run_id FROM guidance_runs WHERE project_root=? AND snapshot_id=? ORDER BY created_at DESC LIMIT 1",
            (str(self.root), snapshot_id),
        ).fetchone()
        if row is None:
            raise ValueError("当前变化快照没有初始化运行，请先调用 knowledge_initialization_start")
        return str(row["run_id"])
