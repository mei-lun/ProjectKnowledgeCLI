from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path
from typing import Any

from .codegraph import CodeGraphClient
from .config import ProjectConfig
from .guidance_models import GuidanceBatch, GuidanceRun
from .guidance_store import GuidanceStore
from .store import KnowledgeStore
from .util import utc_now


class InitializationWorkflow:
    """Persist deterministic CodeGraph batches for an MCP AI client to analyze."""

    MAX_BATCH_FILES = 40

    def __init__(self, project: str | Path, *, client: CodeGraphClient | None = None):
        self.root = Path(project).resolve()
        self.config = ProjectConfig.load(self.root)
        self.client = client or CodeGraphClient(self.root, self.config)
        self.db_path = self.root / ".project-kb" / "index.db"

    def _open(self) -> KnowledgeStore:
        store = KnowledgeStore(self.db_path)
        store.initialize()
        return store

    def start(self) -> dict[str, Any]:
        snapshot = self.client.snapshot()
        files = [item for item in snapshot["files"] if not self._excluded(item["path"])]
        snapshot_id = str(snapshot["snapshot_id"])
        project_root = str(self.root)
        with self._open() as store:
            guidance = GuidanceStore(store)
            row = store.connection.execute(
                "SELECT run_id FROM guidance_runs WHERE project_root=? AND snapshot_id=? ORDER BY created_at LIMIT 1",
                (project_root, snapshot_id),
            ).fetchone()
            if row:
                return self._status(guidance, str(row["run_id"]))
            run_id = "run-" + hashlib.sha256(f"{project_root}\0{snapshot_id}".encode()).hexdigest()[:16]
            now = utc_now()
            groups: dict[str, list[dict[str, Any]]] = {}
            for item in files:
                group = item.get("module") or item["path"].split("/", 1)[0]
                groups.setdefault(str(group), []).append(item)
            batches: list[list[dict[str, Any]]] = []
            for group in sorted(groups):
                ordered = sorted(groups[group], key=lambda item: item["path"])
                batches.extend(ordered[index:index + self.MAX_BATCH_FILES] for index in range(0, len(ordered), self.MAX_BATCH_FILES))
            previous_row = store.connection.execute(
                "SELECT run_id FROM guidance_runs WHERE project_root=? ORDER BY created_at DESC, run_id DESC LIMIT 1",
                (project_root,),
            ).fetchone()
            reusable: dict[tuple[str, ...], dict[str, Any]] = {}
            if previous_row:
                for old_batch in guidance.list_batches(str(previous_row["run_id"])):
                    if old_batch.status == "completed" and old_batch.result:
                        reusable[tuple(old_batch.files)] = old_batch.result
            current_hashes = {item["path"]: item["content_hash"] for item in files}
            with store.transaction():
                guidance.create_run(GuidanceRun(run_id, project_root, snapshot_id, "scanning", len(files), 0, now, now))
                for ordinal, items in enumerate(batches):
                    paths = [item["path"] for item in items]
                    digest = hashlib.sha256(json.dumps([run_id, ordinal, paths], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:16]
                    old_result = reusable.get(tuple(paths))
                    unchanged = bool(old_result) and old_result.get("file_hashes") == {
                        path: current_hashes[path] for path in paths
                    }
                    guidance.save_batch(GuidanceBatch(
                        f"batch-{digest}", run_id, ordinal,
                        "completed" if unchanged else "pending", paths, snapshot_id, now, now,
                        result=old_result if unchanged else None,
                    ))
                run = guidance.get_run(run_id)
                assert run is not None
                current_batches = guidance.list_batches(run_id)
                run.covered_files = sum(len(item.files) for item in current_batches if item.status == "completed")
                if current_batches and all(item.status == "completed" for item in current_batches):
                    run.status = "category_review"
                if not batches:
                    run.status = "category_review"
                run.updated_at = now
                guidance.create_run(run)
            return self._status(guidance, run_id)

    def next_batch(self, run_id: str) -> dict[str, Any]:
        snapshot = self.client.snapshot()
        by_path = {item["path"]: item for item in snapshot["files"]}
        with self._open() as store:
            guidance = GuidanceStore(store)
            run = guidance.get_run(run_id)
            if run is None:
                raise KeyError(f"运行不存在：{run_id}")
            if run.snapshot_id != snapshot["snapshot_id"]:
                raise ValueError("CodeGraph 快照已变化，请重新开始初始化")
            batch = guidance.next_pending_batch(run_id)
            if batch is None:
                return {**self._status(guidance, run_id), "batch": None}
            facts = [by_path[path] for path in batch.files if path in by_path]
            return {
                **self._status(guidance, run_id),
                "batch": batch.to_dict(),
                "file_facts": facts,
                "symbol_summary": {item["path"]: item.get("symbols", []) for item in facts},
                "source_hints": [{"path": path, "use": "按需调用 CodeGraph source"} for path in batch.files],
            }

    def submit_batch(self, run_id: str, batch_id: str, snapshot_id: str,
                     candidates: list[dict[str, Any]], *, error: str | None = None) -> dict[str, Any]:
        with self._open() as store:
            guidance = GuidanceStore(store)
            run = guidance.get_run(run_id)
            if run is None:
                raise KeyError(f"运行不存在：{run_id}")
            batch = next((item for item in guidance.list_batches(run_id) if item.batch_id == batch_id), None)
            if batch is None:
                raise KeyError(f"批次不存在：{batch_id}")
            if snapshot_id != run.snapshot_id or snapshot_id != batch.snapshot_id:
                raise ValueError("提交快照与运行不一致")
            snapshot = self.client.snapshot()
            if snapshot["snapshot_id"] != run.snapshot_id:
                raise ValueError("CodeGraph 快照已变化，请重新开始初始化")
            hashes = {item["path"]: item["content_hash"] for item in snapshot["files"]}
            for candidate in candidates:
                self._validate_candidate(candidate, set(batch.files), hashes)
            now = utc_now()
            batch.status = "failed" if error else "completed"
            batch.result = {
                "candidates": candidates,
                "file_hashes": {path: hashes[path] for path in batch.files if path in hashes},
            }
            batch.error = error
            batch.updated_at = now
            with store.transaction():
                guidance.save_batch(batch)
                batches = guidance.list_batches(run_id)
                run.covered_files = sum(len(item.files) for item in batches if item.status == "completed")
                run.uncovered_files = sorted(path for item in batches if item.status == "failed" for path in item.files)
                run.status = "failed" if run.uncovered_files else (
                    "category_review" if all(item.status == "completed" for item in batches) else "scanning"
                )
                run.updated_at = now
                guidance.create_run(run)
            return self._status(guidance, run_id)

    def _status(self, guidance: GuidanceStore, run_id: str) -> dict[str, Any]:
        run = guidance.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        batches = guidance.list_batches(run_id)
        return {
            **run.to_dict(),
            "batches": [item.to_dict() for item in batches],
            "ready_for_category_draft": (
                not run.uncovered_files
                and run.covered_files == run.total_files
                and all(item.status == "completed" for item in batches)
            ),
        }

    def _excluded(self, path: str) -> bool:
        if path == ".project-kb" or path.startswith((".project-kb/", ".codegraph/")):
            return True
        return any(
            fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch("/" + path, pattern)
            for pattern in self.config.exclude
        )

    @staticmethod
    def _validate_candidate(
        candidate: dict[str, Any], batch_files: set[str], snapshot_hashes: dict[str, str]
    ) -> None:
        required = ("category_id", "name", "purpose", "evidence", "confidence")
        missing = [name for name in required if name not in candidate]
        if missing:
            raise ValueError("候选类别缺少字段：" + "、".join(missing))
        confidence = candidate["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        evidence = candidate["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("候选类别必须包含证据")
        for item in evidence:
            if not isinstance(item, dict) or item.get("path") not in batch_files:
                raise ValueError("候选类别证据必须属于当前批次并包含 hash")
            path = str(item["path"])
            if item.get("hash") != snapshot_hashes.get(path):
                raise ValueError(f"候选类别证据 hash 与当前 CodeGraph 快照不一致：{path}")
