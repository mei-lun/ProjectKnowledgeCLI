from __future__ import annotations

from pathlib import Path
from typing import Any

from .codegraph import CodeGraphClient
from .config import ProjectConfig
from .guidance_models import TaskCompletion
from .guidance_store import GuidanceStore
from .store import KnowledgeStore
from .util import utc_now


class TaskCompletionWorkflow:
    """Persist task completion facts and prepare evidence for guidance drafts."""

    def __init__(self, project: str | Path):
        self.root = Path(project).resolve()
        self.config = ProjectConfig.load(self.root)
        self.client = CodeGraphClient(self.root, self.config)
        self.db_path = self.root / ".project-kb" / "index.db"

    def _open(self) -> KnowledgeStore:
        store = KnowledgeStore(self.db_path)
        store.initialize()
        return store

    def complete(
        self,
        task_id: str,
        summary: str,
        *,
        changed_files: list[str] | None = None,
        changed_symbols: list[str] | None = None,
        tests: list[dict[str, Any]] | None = None,
        user_confirmed: bool = True,
        skip: bool = False,
        skip_reason: str | None = None,
    ) -> dict[str, Any]:
        if not user_confirmed:
            raise ValueError("必须先得到用户完成确认")
        task_id = str(task_id).strip()
        summary = str(summary).strip()
        if not task_id or not summary:
            raise ValueError("task_id 和 summary 不能为空")
        changed_files = sorted({str(path).replace("\\", "/") for path in (changed_files or []) if str(path).strip()})
        changed_symbols = sorted({str(symbol) for symbol in (changed_symbols or []) if str(symbol).strip()})
        tests = [dict(item) for item in (tests or [])]
        snapshot = self.client.snapshot()
        final_snapshot_id = str(snapshot["snapshot_id"])
        current_files = {str(item["path"]): item for item in snapshot.get("files", [])}
        base_snapshot_id = final_snapshot_id
        with self._open() as store:
            guidance = GuidanceStore(store)
            existing = guidance.get_task_completion(task_id)
            if existing is not None:
                return self._result(existing, guidance)
            raw_baseline = store.get_meta("guidance_snapshot")
            if raw_baseline:
                try:
                    import json
                    base_snapshot_id = str(json.loads(raw_baseline).get("snapshot_id") or final_snapshot_id)
                except (TypeError, ValueError):
                    base_snapshot_id = final_snapshot_id
            categories = guidance.list_categories()
            affected = self._affected_categories(categories, changed_files)
            status = "skipped" if skip else "pending"
            now = utc_now()
            task = TaskCompletion(
                task_id=task_id, project_root=str(self.root), summary=summary,
                changed_files=changed_files, changed_symbols=changed_symbols, tests=tests,
                base_snapshot_id=base_snapshot_id, final_snapshot_id=final_snapshot_id,
                user_confirmed=True, generation_status=status,
                affected_categories=affected, created_at=now, updated_at=now,
                skip_reason=skip_reason if skip else None,
            )
            with store.transaction():
                guidance.save_task_completion(task)
            result = self._result(task, guidance)
            result["evidence"] = [
                {"path": path, "hash": current_files[path].get("content_hash") or current_files[path].get("hash"),
                 "language": current_files[path].get("language"), "symbols": current_files[path].get("symbols", [])}
                for path in changed_files if path in current_files
            ]
            result["next_action"] = "none" if skip or not affected else "generate_guidance_draft"
            return result

    def register_pending(self, task_id: str, summary: str) -> dict[str, Any]:
        """Register a hook-observed task without claiming user confirmation."""
        task_id = str(task_id).strip()
        summary = str(summary).strip()
        if not task_id or not summary:
            raise ValueError("task_id 和 summary 不能为空")
        snapshot = self.client.snapshot()
        snapshot_id = str(snapshot["snapshot_id"])
        now = utc_now()
        with self._open() as store:
            guidance = GuidanceStore(store)
            existing = guidance.get_task_completion(task_id)
            if existing is not None:
                return self._result(existing, guidance)
            task = TaskCompletion(
                task_id=task_id, project_root=str(self.root), summary=summary,
                changed_files=[], changed_symbols=[], tests=[],
                base_snapshot_id=snapshot_id, final_snapshot_id=snapshot_id,
                user_confirmed=False, generation_status="pending",
                affected_categories=[], created_at=now, updated_at=now,
            )
            with store.transaction():
                guidance.save_task_completion(task)
            result = self._result(task, guidance)
            result["next_action"] = "confirm_task_completion"
            return result

    @staticmethod
    def _affected_categories(categories: list[Any], changed_files: list[str]) -> list[str]:
        changed = set(changed_files)
        affected: list[str] = []
        for category in categories:
            evidence = {
                str(item.get("path"))
                for item in category.evidence
                if isinstance(item, dict) and item.get("path")
            }
            samples = set(category.samples)
            if changed & (evidence | samples):
                affected.append(category.category_id)
        return sorted(affected)

    @staticmethod
    def _result(task: TaskCompletion, guidance: GuidanceStore) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "generation_status": task.generation_status,
            "user_confirmed": task.user_confirmed,
            "final_snapshot_id": task.final_snapshot_id,
            "affected_categories": list(task.affected_categories),
            "summary": task.summary,
            "skip_reason": task.skip_reason,
            "error": task.error,
            "task": task.to_dict(),
            "pending_drafts": [
                draft.to_dict() for draft in guidance.list_pending_drafts()
                if draft.payload.get("_task_id") == task.task_id
            ],
        }

    def plan(self, selected: list[str] | None = None) -> dict[str, Any]:
        with self._open() as store:
            guidance = GuidanceStore(store)
            categories = guidance.list_categories()
            candidates = [category.to_dict() for category in categories]
            available = {category.category_id for category in categories}
            selected_ids = sorted({str(item) for item in (selected or [])})
            unknown = sorted(set(selected_ids) - available)
            if unknown:
                raise ValueError("选择了不存在的类别：" + "、".join(unknown))
            next_drafts = [
                {"category_id": category_id, "kind": kind}
                for category_id in selected_ids
                for kind in ("methodology", "guidance")
            ]
            return {
                "categories": candidates,
                "selected": selected_ids,
                "next_drafts": next_drafts,
                "next_action": "generate_methodology" if next_drafts else ("select_categories" if candidates else "start_initialization"),
            }
