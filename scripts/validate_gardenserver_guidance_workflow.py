from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from project_knowledge.codegraph import CodeGraphClient
from project_knowledge.config import ProjectConfig
from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.initialization import InitializationWorkflow
from project_knowledge.store import KnowledgeStore


IGNORED_ROOTS = {".project-kb", ".codegraph"}


def business_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file() and not set(path.relative_to(root).parts).intersection(IGNORED_ROOTS)
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


def main() -> int:
    raw_root = os.environ.get("GARDENSERVER_ROOT", "").strip()
    if not raw_root:
        print("GARDENSERVER_ROOT 未设置", file=sys.stderr)
        return 2
    root = Path(raw_root).resolve()
    if not root.is_dir():
        print(f"gardenserver 目录不存在：{root}", file=sys.stderr)
        return 2

    before = business_fingerprint(root)
    config = ProjectConfig.load(root)
    client = CodeGraphClient(root, config)
    codegraph_status = client.status()
    snapshot = client.snapshot()
    workflow = InitializationWorkflow(root, client=client)
    initialized = workflow.start()
    next_batch = workflow.next_batch(initialized["run_id"])

    pending_drafts = []
    with KnowledgeStore(root / ".project-kb" / "index.db") as store:
        guidance = GuidanceStore(store)
        pending_drafts = [
            {
                "draft_id": draft.draft_id, "kind": draft.kind, "status": draft.status,
                "path": draft.path, "content_hash": draft.content_hash,
            }
            for draft in guidance.list_pending_drafts()
        ]

    after = business_fingerprint(root)
    if before != after:
        raise RuntimeError("验收期间 gardenserver 业务文件发生变化")
    if not codegraph_status.get("initialized"):
        raise RuntimeError("CodeGraph 尚未初始化")
    if codegraph_status.get("pendingChanges") not in (None, {"added": 0, "modified": 0, "removed": 0}):
        raise RuntimeError(f"CodeGraph 存在待同步变化：{codegraph_status.get('pendingChanges')}")
    if not snapshot["files"]:
        raise RuntimeError("CodeGraph 快照为空")
    if next_batch.get("batch") and not next_batch.get("file_facts"):
        raise RuntimeError("初始化批次没有返回事实包")
    for draft in pending_drafts:
        path = Path(draft["path"])
        path.relative_to(root / ".project-kb")
        if not path.is_file():
            raise RuntimeError(f"待审核草稿不存在：{path}")

    result = {
        "status": "pass", "project_root": str(root),
        "business_fingerprint": before,
        "codegraph": {
            "version": codegraph_status.get("version"),
            "file_count": codegraph_status.get("fileCount"),
            "node_count": codegraph_status.get("nodeCount"),
            "edge_count": codegraph_status.get("edgeCount"),
            "pending_changes": codegraph_status.get("pendingChanges"),
        },
        "snapshot": {"snapshot_id": snapshot["snapshot_id"], "files_in_scope": len(snapshot["files"])},
        "initialization": {
            "run_id": initialized["run_id"], "status": initialized["status"],
            "total_files": initialized["total_files"], "covered_files": initialized["covered_files"],
            "batches": len(initialized["batches"]),
            "next_batch": next_batch.get("batch", {}).get("batch_id") if next_batch.get("batch") else None,
            "fact_count": len(next_batch.get("file_facts", [])),
        },
        "pending_drafts": pending_drafts,
        "business_source_unchanged": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
