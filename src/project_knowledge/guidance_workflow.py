from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .codegraph import CodeGraphClient
from .config import ProjectConfig
from .guidance_models import GuidanceCategory, GuidanceDraft, GuidanceVersion
from .guidance_store import GuidanceStore
from .models import KnowledgeRecord, SourceReference
from .store import KnowledgeStore
from .util import atomic_write, hash_text, utc_now


class GuidanceWorkflow:
    MACHINE_START = "<!-- project-kb:guidance-state:start -->"
    MACHINE_END = "<!-- project-kb:guidance-state:end -->"
    PROJECT_LEAKAGE_TERMS = (
        "src/", "appsrv", "appmod", "avatar_def", "component", "system",
        "skynet", "zn.", "magent", "worksheet", ".lua", "gardenserver",
    )

    def __init__(self, project: str | Path, *, client: CodeGraphClient | None = None):
        self.root = Path(project).resolve()
        self.config = ProjectConfig.load(self.root)
        self.client = client
        self.db_path = self.root / ".project-kb" / "index.db"

    def _open(self) -> KnowledgeStore:
        store = KnowledgeStore(self.db_path)
        store.initialize()
        return store

    def save_draft(self, kind: str, run_id: str, content: dict[str, Any],
                   category_id: str | None = None) -> dict[str, Any]:
        with self._open() as store:
            guidance = GuidanceStore(store)
            run = guidance.get_run(run_id)
            if run is None:
                raise KeyError(f"运行不存在：{run_id}")
            if kind == "category_catalog":
                categories = content.get("categories")
                if not isinstance(categories, list) or not categories:
                    raise ValueError("分类目录必须包含 categories")
                complete = self._validate_categories(run_id, run.snapshot_id, categories, guidance)
                filename = "功能分类目录-待审核.md"
            elif kind in {"methodology", "guidance"}:
                if not category_id:
                    raise ValueError("方法论或项目指导草稿必须指定 category_id")
                category = next((item for item in guidance.list_categories() if item.category_id == category_id), None)
                if category is None:
                    raise KeyError(f"类别不存在：{category_id}")
                category_run = guidance.get_run(category.run_id)
                if category_run is None or category_run.project_root != run.project_root:
                    raise ValueError("类别不属于当前项目")
                if kind == "methodology":
                    complete = self._validate_methodology(content)
                    filename = f"{self._safe_name(category.name)}-方法论-待审核.md"
                else:
                    complete = self._validate_guide(content, run.snapshot_id)
                    filename = f"{self._safe_name(category.name)}-项目事实指导-待审核.md"
            else:
                raise ValueError(f"不支持的草稿类型：{kind}")
            now = utc_now()
            body = (
                self._render_catalog(content, run)
                if kind == "category_catalog"
                else self._render_methodology(content)
                if kind == "methodology"
                else self._render_guide(content)
            )
            content_hash = hash_text(body)
            draft_id = "draft-" + hashlib.sha256(
                json.dumps([run_id, kind, category_id, run.snapshot_id, content_hash], ensure_ascii=False).encode()
            ).hexdigest()[:16]
            path = (self.root / ".project-kb" / filename).resolve()
            self._ensure_project_path(path)
            draft = GuidanceDraft(
                draft_id=draft_id, run_id=run_id, kind=kind,
                status="awaiting_confirmation" if complete else "incomplete",
                path=str(path), content_hash=content_hash, snapshot_id=run.snapshot_id,
                payload={**content, "_body": body}, created_at=now, updated_at=now,
                category_id=category_id,
            )
            atomic_write(path, self._with_machine_state(draft, body))
            with store.transaction():
                guidance.save_draft(draft)
                run.status = "category_review" if kind == "category_catalog" else "guidance_review"
                run.updated_at = now
                guidance.create_run(run)
            return {
                "status": draft.status, "draft_id": draft_id, "content_hash": content_hash,
                "path": str(path), "next_actions": ["打开 Markdown 审核", "确认正文哈希后提交"],
            }

    def confirm_draft(self, draft_id: str, content_hash: str, reviewer: str) -> dict[str, Any]:
        if not reviewer.strip():
            raise ValueError("reviewer 不能为空")
        with self._open() as store:
            guidance = GuidanceStore(store)
            draft = guidance.get_draft(draft_id)
            if draft is None:
                raise KeyError(f"草稿不存在：{draft_id}")
            if draft.status == "confirmed":
                formal_path = draft.payload.get("_formal_path", draft.path)
                return {"status": "confirmed", "draft_id": draft_id, "content_hash": draft.content_hash, "path": formal_path, "next_actions": []}
            if draft.status != "awaiting_confirmation":
                raise ValueError("草稿不完整或已拒绝，不能确认")
            path = Path(draft.path)
            self._ensure_project_path(path)
            disk_body = self._body_from_document(path.read_text(encoding="utf-8"))
            disk_hash = hash_text(disk_body)
            if content_hash != draft.content_hash or disk_hash != draft.content_hash:
                raise ValueError("草稿正文哈希已变化，请重新保存草稿后审核")
            run = guidance.get_run(draft.run_id)
            if run is None or run.snapshot_id != draft.snapshot_id:
                raise ValueError("草稿快照已过期")
            now = utc_now()
            if draft.kind == "category_catalog":
                formal = self.root / ".project-kb" / "功能分类目录.md"
                with store.transaction():
                    existing_categories = {item.category_id: item for item in guidance.list_categories()}
                    for item in draft.payload["categories"]:
                        category = self._category_from(item, draft.run_id, now)
                        existing = existing_categories.get(category.category_id)
                        if existing is not None:
                            category.run_id = existing.run_id
                            category.created_at = existing.created_at
                        guidance.save_category(category)
                    draft.status = "confirmed"
                    draft.payload["_formal_path"] = str(formal.resolve())
                    draft.confirmed_at = now
                    draft.updated_at = now
                    guidance.save_draft(draft)
                    run.status = "guidance_generation" if draft.payload.get("_change_id") else "categories_confirmed"
                    run.updated_at = now
                    guidance.create_run(run)
                    atomic_write(formal, disk_body)
                path.unlink(missing_ok=True)
                return {"status": "confirmed", "draft_id": draft_id, "content_hash": content_hash, "path": str(formal.resolve()), "next_actions": ["逐类别生成开发指导草稿"]}
            return self._confirm_asset(store, guidance, draft, run, disk_body, now)

    def reject_draft(self, draft_id: str, reviewer: str, reason: str) -> dict[str, Any]:
        if not reviewer.strip() or not reason.strip():
            raise ValueError("拒绝草稿必须提供 reviewer 和 reviewReason")
        with self._open() as store:
            guidance = GuidanceStore(store)
            draft = guidance.get_draft(draft_id)
            if draft is None:
                raise KeyError(f"草稿不存在：{draft_id}")
            draft.status = "rejected"
            draft.rejection_reason = f"{reviewer}: {reason}"
            draft.updated_at = utc_now()
            with store.transaction():
                guidance.save_draft(draft)
            return {"status": "rejected", "draft_id": draft_id, "path": draft.path, "next_actions": ["修改后重新保存草稿"]}

    def _confirm_asset(self, store: KnowledgeStore, guidance: GuidanceStore, draft: GuidanceDraft,
                       run: Any, body: str, now: str) -> dict[str, Any]:
        assert draft.category_id
        category = next(item for item in guidance.list_categories() if item.category_id == draft.category_id)
        asset_type = "methodology" if draft.kind == "methodology" else "project_guidance"
        number = max((item.version for item in guidance.list_versions(category.category_id, asset_type)), default=0) + 1
        prefix = "methodology" if asset_type == "methodology" else "guide"
        version_id = f"{prefix}-{category.category_id}-v{number}"
        title_suffix = "轻量方法论" if asset_type == "methodology" else "项目事实指导"
        version = GuidanceVersion(
            version_id, category.category_id, number, f"{category.name}{title_suffix}",
            body, draft.content_hash, draft.snapshot_id,
            list(draft.payload.get("evidence", [])), True, now, draft.draft_id, asset_type,
        )
        formal = self.root / ".project-kb" / (
            f"{self._safe_name(category.name)}-方法论.md"
            if asset_type == "methodology"
            else f"{self._safe_name(category.name)}-项目事实指导.md"
        )
        sources = [
            SourceReference(type="file", path=item["path"], hash=item.get("hash"))
            for item in draft.payload.get("evidence", []) if isinstance(item, dict) and item.get("path")
        ]
        record = KnowledgeRecord(
            id=f"{prefix}.{category.category_id}",
            kind="development-methodology" if asset_type == "methodology" else "development-guide",
            title=f"{category.name}{title_suffix}", path=str(formal.relative_to(self.root).as_posix()),
            ownership="curated", confidence="generated" if asset_type == "methodology" else "verified", status="fresh",
            sources=sources, source_hashes={item.path: item.hash for item in sources if item.path and item.hash},
            last_generated_at=now, last_verified_at=now,
            tags=[category.category_id, category.name, title_suffix], content=body,
        )
        previous = formal.read_text(encoding="utf-8") if formal.exists() else None
        try:
            with store.transaction():
                guidance.save_version(version)
                store.upsert_knowledge(record)
                draft.status = "confirmed"
                draft.payload["_formal_path"] = str(formal.resolve())
                draft.confirmed_at = now
                draft.updated_at = now
                guidance.save_draft(draft)
                remaining = [
                    item for item in guidance.list_pending_drafts(run.run_id)
                    if item.kind in {"methodology", "guidance"}
                    and item.draft_id != draft.draft_id
                ]
                run.status = "guidance_review" if remaining else "complete"
                run.updated_at = now
                guidance.create_run(run)
                self._complete_linked_change(store, guidance, draft, now)
                atomic_write(formal, body)
        except BaseException:
            if previous is None:
                formal.unlink(missing_ok=True)
            else:
                atomic_write(formal, previous)
            raise
        Path(draft.path).unlink(missing_ok=True)
        return {"status": "confirmed", "draft_id": draft.draft_id, "content_hash": draft.content_hash, "path": str(formal.resolve()), "version_id": version_id, "next_actions": ["通过 knowledge_get 或 knowledge_search 查询正式指导"]}

    @staticmethod
    def _complete_linked_change(store: KnowledgeStore, guidance: GuidanceStore,
                                draft: GuidanceDraft, now: str) -> None:
        change_id = draft.payload.get("_change_id")
        if not change_id:
            return
        files = draft.payload.get("_snapshot_files")
        head = draft.payload.get("_head_snapshot_id")
        if not isinstance(files, dict) or not head:
            raise ValueError("增量草稿缺少快照基线")
        store.set_meta("guidance_snapshot", json.dumps({
            "snapshot_id": head, "files": files,
        }, ensure_ascii=False, sort_keys=True))
        guidance.mark_change_processed(str(change_id), now)

    def _validate_categories(self, run_id: str, snapshot_id: str, categories: list[dict[str, Any]], guidance: GuidanceStore) -> bool:
        batches = guidance.list_batches(run_id)
        hashes = self._snapshot_hashes(snapshot_id)
        covered = {path for batch in batches if batch.status == "completed" for path in batch.files}
        covered.update(hashes)
        complete = bool(not batches or all(batch.status == "completed" for batch in batches))
        for item in categories:
            for key in ("category_id", "name", "purpose", "applies_to", "excludes", "samples", "evidence", "confidence", "unknowns"):
                if key not in item:
                    raise ValueError(f"分类缺少字段：{key}")
            confidence = item["confidence"]
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise ValueError("confidence 必须在 0 到 1 之间")
            for sample in item["samples"]:
                if sample not in covered:
                    raise ValueError(f"样本不在已覆盖文件中：{sample}")
            for evidence in item["evidence"]:
                self._validate_evidence(evidence, covered, hashes)
        return complete

    def _validate_methodology(self, content: dict[str, Any]) -> bool:
        for key in ("basic", "scope", "questions", "starter_checks", "unknowns"):
            if key not in content:
                raise ValueError(f"方法论缺少字段：{key}")
        complete = all(content.get(key) for key in ("basic", "scope", "questions", "starter_checks"))
        methodology_text = json.dumps(content, ensure_ascii=False).lower()
        return complete and not any(term in methodology_text for term in self.PROJECT_LEAKAGE_TERMS)

    def _validate_guide(self, content: dict[str, Any], snapshot_id: str) -> bool:
        for key in ("basic", "methodology_ref", "project_adaptation", "variants", "evidence", "unknowns"):
            if key not in content:
                raise ValueError(f"项目事实指导缺少字段：{key}")
        if "methodology" in content:
            return False
        reference = content["methodology_ref"]
        adaptation = content["project_adaptation"]
        complete = isinstance(reference, dict) and all(reference.get(key) for key in ("id", "title"))
        complete = complete and all(adaptation.get(key) for key in (
            "entrypoints", "locations", "call_flow", "registration", "data_and_config",
            "steps", "invariants", "testing", "release", "rollback",
        ))
        hashes = self._snapshot_hashes(snapshot_id)
        for evidence in content["evidence"]:
            self._validate_evidence(evidence, set(hashes) if hashes else {evidence.get("path")}, hashes)
        return complete

    def _snapshot_hashes(self, snapshot_id: str) -> dict[str, str]:
        if self.client is None:
            return {}
        snapshot = self.client.snapshot()
        if snapshot["snapshot_id"] != snapshot_id:
            raise ValueError("当前 CodeGraph 快照与运行不一致")
        return {item["path"]: item["content_hash"] for item in snapshot["files"]}

    @staticmethod
    def _validate_evidence(evidence: dict[str, Any], allowed: set[str], hashes: dict[str, str]) -> None:
        path = str(evidence.get("path", "")).replace("\\\\", "/").lstrip("./")
        if not path or path.startswith("../") or path not in allowed:
            raise ValueError(f"证据路径无效：{path}")
        if hashes and evidence.get("hash") != hashes.get(path):
            raise ValueError(f"证据 hash 不一致：{path}")

    @staticmethod
    def _category_from(item: dict[str, Any], run_id: str, now: str) -> GuidanceCategory:
        return GuidanceCategory(
            item["category_id"], run_id, item["name"], item["purpose"],
            list(item["applies_to"]), list(item["excludes"]), list(item["samples"]),
            list(item["evidence"]), float(item["confidence"]), list(item["unknowns"]),
            now, now, list(item.get("relations", [])),
        )

    def _with_machine_state(self, draft: GuidanceDraft, body: str) -> str:
        state = json.dumps({
            "draft_id": draft.draft_id, "content_hash": draft.content_hash,
            "snapshot_id": draft.snapshot_id, "status": draft.status,
        }, ensure_ascii=False, sort_keys=True)
        return f"{self.MACHINE_START}\n{state}\n{self.MACHINE_END}\n\n{body}"

    def _body_from_document(self, document: str) -> str:
        pattern = re.escape(self.MACHINE_START) + r".*?" + re.escape(self.MACHINE_END) + r"\n\n"
        return re.sub(pattern, "", document, count=1, flags=re.DOTALL)

    @staticmethod
    def _render_catalog(content: dict[str, Any], run: Any) -> str:
        lines = ["# 功能分类目录", "", f"- 快照：{run.snapshot_id}", f"- 覆盖率：{run.covered_files}/{run.total_files}", ""]
        for item in content["categories"]:
            lines.extend([
                f"## {item['name']}（{item['category_id']}）", "",
                f"**用途**：{item['purpose']}", "",
                "### 适用范围", *[f"- {value}" for value in item["applies_to"]],
                "", "### 不适用范围", *[f"- {value}" for value in item["excludes"]],
                "", "### 样本", *[f"- {value}" for value in item["samples"]],
                "", "### 证据", *[f"- {value['path']}（{value.get('hash', '')}）" for value in item["evidence"]],
                "", f"**置信度**：{item['confidence']}", "",
                "### 待确认事项", *([f"- {value}" for value in item["unknowns"]] or ["- 无"]),
                "", "### 类别关系与合并拆分建议",
                *([f"- {json.dumps(value, ensure_ascii=False)}" for value in item.get("relations", [])] or ["- 无"]), "",
            ])
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_guide(content: dict[str, Any]) -> str:
        title = content["basic"].get("title", "开发指导") if isinstance(content["basic"], dict) else str(content["basic"])
        reference = content["methodology_ref"]
        lines = [
            f"# {title}", "", "## 方法论引用", "",
            f"- {reference['title']}（知识 ID：`{reference['id']}`）", "",
            "## 当前项目事实指导", "",
        ]
        labels = {
            "entrypoints": "入口", "locations": "代码位置", "call_flow": "调用流程",
            "registration": "注册方式", "data_and_config": "数据与配置", "steps": "项目实施步骤",
            "invariants": "项目不变量", "testing": "项目测试", "release": "发布", "rollback": "回滚",
        }
        for key, label in labels.items():
            lines.extend([f"### {label}", GuidanceWorkflow._markdown(content["project_adaptation"].get(key)), ""])
        lines.extend(["## 变体", GuidanceWorkflow._markdown(content["variants"]) or "- 无", "", "## 证据"])
        lines.extend([f"- {item['path']}（{item.get('hash', '')}）" for item in content["evidence"]])
        lines.extend(["", "## 未确认事项", GuidanceWorkflow._markdown(content["unknowns"]) or "- 无", ""])
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_methodology(content: dict[str, Any]) -> str:
        title = content["basic"].get("title", "轻量方法论") if isinstance(content["basic"], dict) else str(content["basic"])
        lines = [f"# {title}", "", "## 当前范围", GuidanceWorkflow._markdown(content["scope"]), ""]
        lines.extend(["## 首次对齐问题", GuidanceWorkflow._markdown(content["questions"]), ""])
        lines.extend(["## 起步检查", GuidanceWorkflow._markdown(content["starter_checks"]), ""])
        lines.extend(["## 待用户二次对齐", GuidanceWorkflow._markdown(content["unknowns"]) or "- 无", ""])
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _markdown(value: Any) -> str:
        if value is None or value == []:
            return ""
        if isinstance(value, list):
            return "\n".join(f"- {item}" for item in value)
        if isinstance(value, dict):
            return "\n".join(f"- **{key}**：{item}" for key, item in value.items())
        return str(value)

    @staticmethod
    def _safe_name(value: str) -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")
        return name[:80] or "未命名类别"

    def _ensure_project_path(self, path: Path) -> None:
        root = (self.root / ".project-kb").resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError as error:
            raise ValueError("草稿路径越界") from error
