from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .codegraph import CodeGraphClient
from .config import ProjectConfig
from .guidance_models import GuidanceChange, GuidanceRun, GuidanceVersion
from .guidance_store import GuidanceStore
from .guidance_workflow import GuidanceWorkflow
from .models import SourceReference
from .store import KnowledgeStore
from .util import utc_now


class IncrementalWorkflow:
    """Build change-scoped fact packs and apply three update levels."""

    BASELINE_KEY = "guidance_snapshot"

    def _node_path(self, item: dict[str, Any]) -> str:
        node = item.get("node", item)
        raw = str(node.get("filePath", node.get("path", ""))).replace("\\", "/")
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                return candidate.resolve().relative_to(self.root).as_posix()
            except ValueError:
                return ""
        return raw.removeprefix("./")

    def __init__(self, project: str | Path, *, client: CodeGraphClient | None = None):
        self.root = Path(project).resolve()
        self.config = ProjectConfig.load(self.root)
        self.client = client or CodeGraphClient(self.root, self.config)
        self.db_path = self.root / ".project-kb" / "index.db"

    def _open(self) -> KnowledgeStore:
        store = KnowledgeStore(self.db_path)
        store.initialize()
        return store

    def changes(self, *, retry: bool = False) -> dict[str, Any]:
        current = self.client.snapshot()
        current_files = {item["path"]: item for item in current["files"]}
        with self._open() as store:
            guidance = GuidanceStore(store)
            for pending in guidance.pending_changes():
                if pending.head_snapshot_id == current["snapshot_id"] and not pending.payload.get("error"):
                    return {**pending.payload, "status": "pending", **self.plan_from_store(store, pending)}
                if pending.head_snapshot_id != current["snapshot_id"] and not pending.payload.get("error"):
                    self._fail_change(store, pending, "CodeGraph snapshot changed; retry against the current snapshot")
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
            if existing and not (retry and existing.payload.get("error")):
                return {**existing.payload, "status": "pending", **self.plan_from_store(store, existing)}
            if existing:
                attempt = store.connection.execute(
                    "SELECT COUNT(*) FROM guidance_changes WHERE base_snapshot_id=? AND head_snapshot_id=?",
                    (base_id, current["snapshot_id"]),
                ).fetchone()[0]
                change_id = "change-" + hashlib.sha256(f"{identity}\0retry-{attempt}".encode()).hexdigest()[:16]
            facts = []
            affected_paths = set(changed)
            for path in added + modified:
                item = current_files[path]
                source = self.client.source(path, start_line=1, limit=400)
                symbols = list(item.get("symbols", []))
                analysis = {"symbol_source": "files", "truncated": len(symbols) > 20, "limitations": []}
                if not symbols:
                    query = getattr(self.client, "query", None)
                    if callable(query):
                        candidates = query(Path(path).stem, limit=50)
                        symbols = [candidate.get("node", candidate) for candidate in candidates
                                   if isinstance(candidate, dict) and self._node_path(candidate) == path]
                        analysis["symbol_source"] = "query"
                        analysis["truncated"] = len(candidates) >= 50 or len(symbols) > 20
                    if not symbols:
                        analysis["limitations"].append("No symbols resolved through the public query; dependency coverage is unknown")
                impacts: list[dict[str, Any]] = []
                for symbol in symbols[:20]:
                    name = (symbol.get("qualifiedName") or symbol.get("name")) if isinstance(symbol, dict) else str(symbol)
                    if not name:
                        continue
                    payload = self.client.impact(str(name), depth=2)
                    candidates = payload.get("affected", []) if isinstance(payload, dict) else []
                    analysis["truncated"] |= len(candidates) > 50 - len(impacts)
                    impacts.extend(candidates[:50 - len(impacts)])
                    affected_paths.update(
                        self._node_path(candidate)
                        for candidate in candidates[:50] if isinstance(candidate, dict) and self._node_path(candidate)
                    )
                    if len(impacts) >= 50:
                        break
                facts.append({
                    "path": path, "change": "added" if path in added else "modified",
                    "hash": item["content_hash"], "language": item["language"],
                    "symbols": symbols[:20], "source": source, "impact": impacts[:50], "analysis": analysis,
                })
            facts.extend({"path": path, "change": "deleted", "old_hash": old_files[path]} for path in deleted)
            affected_categories = []
            classified_paths: set[str] = set()
            confirmed_catalog_categories = {
                item["category_id"]
                for row in store.connection.execute("SELECT payload_json FROM guidance_drafts WHERE kind='category_catalog' AND status='confirmed'")
                for item in json.loads(row["payload_json"]).get("categories", [])
            }
            for category in guidance.list_categories():
                if category.run_id.startswith("run-incr-") and category.category_id not in confirmed_catalog_categories:
                    continue
                evidence_paths = {item.get("path") for item in category.evidence if isinstance(item, dict)}
                if evidence_paths & affected_paths or set(category.samples) & affected_paths:
                    affected_categories.append(category.category_id)
                classified_paths.update(evidence_paths | set(category.samples))
            unclassified = sorted(set(changed) - classified_paths)
            run_id = "run-incr-" + hashlib.sha256(f"{self.root}\0{change_id}".encode()).hexdigest()[:16]
            payload = {
                "base_snapshot_id": base_id, "head_snapshot_id": current["snapshot_id"],
                "changed_files": changed, "added": added, "modified": modified, "deleted": deleted,
                "affected_categories": sorted(affected_categories),
                "originalAffectedCategories": sorted(affected_categories),
                "pendingCategories": sorted(affected_categories),
                "completedCategories": [],
                "categoryLevels": {category_id: "guidance" for category_id in sorted(affected_categories)},
                "facts": facts,
                "run_id": run_id,
                "snapshot_files": {path: item["content_hash"] for path, item in current_files.items()},
                "unclassified_files": unclassified,
                "catalogRequired": bool(unclassified),
            }
            now = utc_now()
            change = GuidanceChange(
                change_id, str(self.root), base_id, current["snapshot_id"], "guidance",
                changed, sorted(affected_categories), payload, now,
            )
            with store.transaction():
                guidance.create_run(GuidanceRun(
                    run_id, str(self.root), current["snapshot_id"],
                    "guidance_generation", len(current_files), len(current_files), now, now,
                ))
                guidance.save_change(change)
            return {**payload, "status": "pending", **self.plan_from_store(store, change)}

    @staticmethod
    def _refresh_tasks(store: KnowledgeStore, change_id: str) -> None:
        from . import task_workflow
        refresh = getattr(task_workflow, "refresh_linked_tasks", None)
        if refresh is not None:
            refresh(store, change_id)

    @classmethod
    def _fail_change(cls, store: KnowledgeStore, change: GuidanceChange, reason: str) -> None:
        guidance = GuidanceStore(store)
        with nullcontext() if store.connection.in_transaction else store.transaction():
            change.payload["error"] = reason
            guidance.save_change(change)
            run = guidance.get_run(change.payload.get("run_id", ""))
            if run:
                run.status, run.error, run.updated_at = "failed", reason, utc_now()
                guidance.create_run(run)
            cls._refresh_tasks(store, change.change_id)

    def plan(self, change_id: str) -> dict[str, Any]:
        with self._open() as store:
            row = store.connection.execute("SELECT * FROM guidance_changes WHERE change_id=?", (change_id,)).fetchone()
            if row is None:
                raise KeyError(f"change not found: {change_id}")
            change = GuidanceStore._change(row)
            if not change.processed_at and self.client.snapshot()["snapshot_id"] != change.head_snapshot_id:
                self._fail_change(store, change, "CodeGraph snapshot changed; retry against the current snapshot")
            return self.plan_from_store(store, change)

    @staticmethod
    def draft_slot(kind: str, category_id: str | None) -> str:
        return json.dumps(["category" if kind == "category_catalog" else kind, category_id], separators=(",", ":"))

    @staticmethod
    def plan_from_store(store: KnowledgeStore, change: GuidanceChange | str) -> dict[str, Any]:
        guidance = GuidanceStore(store)
        if isinstance(change, str):
            row = store.connection.execute("SELECT * FROM guidance_changes WHERE change_id=?", (change,)).fetchone()
            if row is None:
                raise KeyError(f"change not found: {change}")
            change = guidance._change(row)
        payload = change.payload
        latest = {}
        for row in store.connection.execute("SELECT * FROM guidance_drafts ORDER BY rowid"):
            draft = guidance._draft(row)
            if draft.payload.get("_change_id") == change.change_id:
                latest[("category" if draft.kind == "category_catalog" else draft.kind, draft.category_id)] = draft
        for slot, draft_id in payload.get("latestDrafts", {}).items():
            kind, category_id = json.loads(slot)
            draft = guidance.get_draft(draft_id)
            if draft is not None and draft.payload.get("_change_id") == change.change_id:
                latest[(kind, category_id)] = draft
        categories = sorted(set(payload.get("pendingCategories", change.affected_categories)) | set(payload.get("completedCategories", [])))
        keys = [("category", None)] if payload.get("catalogRequired") else []
        for category_id in categories:
            if category_id in payload.get("methodologyCategories", []):
                keys.append(("methodology", category_id))
            keys.append(("guidance", category_id))
        items = []
        for kind, category_id in keys:
            draft = latest.get((kind, category_id))
            state = "processed" if kind == "guidance" and category_id in payload.get("completedCategories", []) else "pending_generation"
            if draft and draft.status in {"awaiting_confirmation", "confirmed"}:
                state = "processed" if draft.status == "confirmed" else "awaiting_review"
            items.append({"category_id": category_id, "kind": kind, "status": state,
                          "draft_id": draft.draft_id if draft else None})
        remaining = [item for item in items if item["status"] == "pending_generation"]
        hashes = payload.get("snapshot_files", {})
        evidence_paths = {item["path"] for item in payload.get("facts", []) if item.get("hash")}
        for category in guidance.list_categories():
            if category.category_id in categories:
                evidence_paths.update(category.samples)
                evidence_paths.update(item["path"] for item in category.evidence if item.get("path"))
        evidence = [{"path": path, "hash": hashes[path]} for path in sorted(evidence_paths) if path in hashes]
        result = {"change_id": change.change_id, "run_id": payload.get("run_id"),
                  "head_snapshot_id": change.head_snapshot_id, "items": items, "remaining": remaining,
                  "generation_complete": bool(items) and not remaining,
                  "facts": payload.get("facts", []), "evidence": evidence,
                  "current_guidance": [version.to_dict() for category in categories
                                       if (version := guidance.current_version(category)) is not None],
                  "next_action": "await_review", "next_tool": None, "arguments": {}}
        if payload.get("superseded_by"):
            result.update(next_action="superseded", generation_complete=False, superseded_by=payload["superseded_by"])
        elif payload.get("error"):
            result.update(next_action="retry", generation_complete=False, error=payload["error"])
        elif change.processed_at:
            result.update(next_action="complete", generation_complete=True)
        elif remaining:
            item = remaining[0]
            if not evidence:
                result.update(next_action="manual_review", reason="Only deletion facts remain; provide surviving evidence and a reviewed classification or guidance decision")
            else:
                arguments = {"projectPath": change.project_root, "changeId": change.change_id, "level": item["kind"]}
                if item["category_id"]:
                    arguments["categoryId"] = item["category_id"]
                result.update(next_action=f"generate_{item['kind']}",
                              next_tool="knowledge_update_submit", arguments=arguments)
        return result

    def submit_update(self, change_id: str, level: str, *, category_id: str | None = None,
                      content: dict[str, Any] | None = None,
                      evidence: list[dict[str, Any]] | None = None,
                      task_id: str | None = None) -> dict[str, Any]:
        content = content or {}
        evidence = evidence or []
        with self._open() as store:
            guidance = GuidanceStore(store)
            change = next((item for item in guidance.pending_changes() if item.change_id == change_id), None)
            if change is None:
                raise KeyError(f"待处理变化不存在：{change_id}")
            if task_id:
                task = guidance.get_task_completion(task_id)
                if (task is None or task.change_id != change_id or not task.development_completed
                        or task.generation_status in {"failed", "skipped"}):
                    raise ValueError("task association is invalid for this change")
            if level not in {"fact", "guidance", "methodology", "category"}:
                raise ValueError(f"更新级别无效：{level}")
            snapshot = self.client.snapshot()
            if change.payload.get("error") or snapshot["snapshot_id"] != change.head_snapshot_id:
                self._fail_change(store, change, "CodeGraph snapshot changed; retry against the current snapshot")
                raise ValueError("CodeGraph snapshot changed; retry the change")
            plan = self.plan_from_store(store, change)
            missing = [item["category_id"] for item in plan["remaining"] if item["kind"] != "category"]
            selected_category = category_id or (missing[0] if missing else None)
            if level != "category" and (not missing or selected_category != missing[0]):
                raise ValueError("updates must process pending categories in order")
            if level != "category" and any(item["kind"] == "category" for item in plan["remaining"]):
                raise ValueError("classify unclassified changes before updating guidance")
            if level != "category" and plan["remaining"][0]["kind"] != ("guidance" if level == "fact" else level):
                raise ValueError("generate the required methodology before guidance")
            if level == "fact":
                with store.transaction():
                    result = self._submit_fact(store, guidance, change, category_id, content, evidence)
                    change.payload.setdefault("factDecisions", {})[selected_category] = {
                        "reason": content.get("reason", "Caller verified that guidance is unchanged"), "evidence": evidence,
                    }
                    self._advance(store, guidance, change, selected_category)
                result.update(self.plan_from_store(store, change.change_id))
                if task_id and result.get("next_tool") == "knowledge_update_submit":
                    result["arguments"]["taskId"] = task_id
                return result
            run_id = change.payload.get("run_id") or self._run_for_snapshot(store, change.head_snapshot_id)
            linked_content = {key: value for key, value in content.items() if not key.startswith("_")}
            linked_content["_change_id"] = change_id
            if task_id:
                linked_content["_task_id"] = task_id
            linked_content["_head_snapshot_id"] = change.head_snapshot_id
            linked_content["_snapshot_files"] = {
                item["path"]: item["content_hash"] for item in snapshot["files"]
            }
            if level in {"guidance", "methodology"}:
                if not category_id:
                    raise ValueError("guidance 更新必须指定 categoryId")
                result = GuidanceWorkflow(self.root, client=self.client).save_draft(level, run_id, linked_content, category_id)
                result.update({"change_id": change_id, "level": level})
                result.update(self.plan_from_store(store, change_id))
                if task_id and result.get("next_tool") == "knowledge_update_submit":
                    result["arguments"]["taskId"] = task_id
                return result
            result = GuidanceWorkflow(self.root, client=self.client).save_draft("category_catalog", run_id, linked_content)
            result.update({"change_id": change_id, "level": level})
            result.update(self.plan_from_store(store, change_id))
            if task_id and result.get("next_tool") == "knowledge_update_submit":
                result["arguments"]["taskId"] = task_id
            return result

    def _submit_fact(self, store: KnowledgeStore, guidance: GuidanceStore, change: GuidanceChange,
                     category_id: str | None, content: dict[str, Any],
                     evidence: list[dict[str, Any]]) -> dict[str, Any]:
        if not category_id or not content.get("guidance_unchanged"):
            raise ValueError("fact 更新必须声明 categoryId 和 guidance_unchanged=true")
        if not evidence:
            raise ValueError("fact 更新必须提供有效证据")
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
        with nullcontext() if store.connection.in_transaction else store.transaction():
            guidance.save_version(version)
            store.upsert_knowledge(record)
        return {"status": "completed", "change_id": change.change_id, "level": "fact", "version_id": version.version_id, "next_actions": []}

    def _advance(self, store: KnowledgeStore, guidance: GuidanceStore,
                 change: GuidanceChange, category_id: str) -> None:
        now = utc_now()
        with nullcontext() if store.connection.in_transaction else store.transaction():
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
            self._refresh_tasks(store, change.change_id)

    def _advance_baseline(self, store: KnowledgeStore, guidance: GuidanceStore,
                          change: GuidanceChange, now: str) -> None:
        snapshot = self.client.snapshot()
        if snapshot["snapshot_id"] != change.head_snapshot_id:
            raise ValueError("CodeGraph snapshot changed before baseline advancement")
        if change.payload.get("catalogRequired"):
            plan = self.plan_from_store(store, change)
            if any(item["status"] != "processed" for item in plan["items"]):
                return
        baseline = {
            "snapshot_id": snapshot["snapshot_id"],
            "files": {item["path"]: item["content_hash"] for item in snapshot["files"]},
        }
        store.set_meta(self.BASELINE_KEY, json.dumps(baseline, ensure_ascii=False, sort_keys=True))
        guidance.mark_change_processed(change.change_id, now)
        self._retire_failed_changes(store, change, now)

    @staticmethod
    def _retire_failed_changes(store: KnowledgeStore, completed: GuidanceChange, now: str) -> None:
        guidance = GuidanceStore(store)
        for previous in guidance.pending_changes():
            if (previous.change_id != completed.change_id and previous.project_root == completed.project_root
                    and previous.base_snapshot_id == completed.base_snapshot_id and previous.payload.get("error")):
                previous.payload["superseded_by"] = completed.change_id
                previous.processed_at = now
                guidance.save_change(previous)
                IncrementalWorkflow._refresh_tasks(store, previous.change_id)

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
