from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .models import PatchOperation, Proposal
from .schemas import FEATURE_GUIDE_DRAFT_SCHEMA, PROPOSAL_SCHEMA, validate_instance
from .util import atomic_json, atomic_write, hash_file, hash_text, project_lock, read_text, utc_now


PROPOSAL_ID_PATTERN = re.compile(r"^kp-[0-9a-f]{16}$")
BLOCK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MISSING_HASH = "missing"


class ProposalNotFoundError(ValueError):
    pass


class ProposalStateError(ValueError):
    pass


class ProposalConflictError(ValueError):
    pass


class ProposalService:
    """Store and apply reviewable semantic knowledge changes."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.directory = self.root / ".project-kb" / "proposals"
        self.queue_directory = self.directory / "queue"

    def create(
        self, *, target: str, reason: str, evidence: Iterable[str],
        operations: Iterable[PatchOperation | dict[str, Any]], confidence: float = 0.8,
        change_range: str | None = None, dry_run: bool = False,
    ) -> Proposal | dict[str, Any]:
        target_path, relative_target = self._target_path(target)
        normalized_evidence = sorted({item.strip() for item in evidence if item.strip()})
        normalized_operations = [
            item if isinstance(item, PatchOperation) else PatchOperation.from_dict(item)
            for item in operations
        ]
        self._validate_intent(
            target_path, relative_target, reason, normalized_evidence,
            normalized_operations, confidence,
        )
        target_hash = hash_file(target_path) if target_path.exists() else MISSING_HASH
        source_hashes = self._source_hashes(normalized_evidence)
        intent = {
            "target": relative_target, "target_hash": target_hash, "reason": reason.strip(),
            "evidence": normalized_evidence, "source_hashes": source_hashes, "confidence": float(confidence),
            "operations": [item.to_dict() for item in normalized_operations],
            "change_range": change_range.strip() if change_range else None,
        }
        canonical = json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        proposal_id = "kp-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        proposal = Proposal(
            proposal_id=proposal_id, target=relative_target, target_hash=target_hash,
            reason=reason.strip(), evidence=normalized_evidence, source_hashes=source_hashes,
            confidence=float(confidence),
            operations=normalized_operations, created_at=utc_now(),
            change_range=change_range.strip() if change_range else None,
        )
        payload = proposal.to_dict()
        validate_instance(payload, PROPOSAL_SCHEMA)
        path = self._proposal_path(proposal_id)
        if path.exists():
            existing = self.get(proposal_id)
            return (
                {**existing.to_dict(), "action": "propose", "dry_run": True, "would_create": False}
                if dry_run else existing
            )
        if dry_run:
            return {**payload, "action": "propose", "dry_run": True, "would_create": True}
        with project_lock(self.root):
            if path.exists():
                return self.get(proposal_id)
            atomic_json(path, payload)
            self._link_queue(proposal)
        return proposal

    def create_from_feature_draft(
        self, feature_id: str, *, change_range: str | None = None, dry_run: bool = False,
    ) -> Proposal | dict[str, Any]:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", feature_id):
            raise ValueError(f"无效 Feature Guide ID：{feature_id}")
        structured_path = self.root / ".project-kb" / "drafts" / "features" / f"{feature_id}.json"
        markdown_path = self.root / "docs" / "knowledge" / "drafts" / "features" / f"{feature_id}.md"
        if not structured_path.exists() or not markdown_path.exists():
            raise ValueError(f"Feature Guide 草案不完整：{feature_id}")
        payload = json.loads(structured_path.read_text(encoding="utf-8"))
        guide = {key: value for key, value in payload.items() if key != "_generation"}
        validate_instance(guide, FEATURE_GUIDE_DRAFT_SCHEMA)
        source_paths = sorted({
            str(source["path"]) for source in self._iter_sources(guide)
            if isinstance(source.get("path"), str)
        })
        evidence = [
            markdown_path.relative_to(self.root).as_posix(),
            structured_path.relative_to(self.root).as_posix(),
            *source_paths,
        ]
        return self.create(
            target=f"docs/knowledge/curated/features/{feature_id}.md",
            reason=f"将已校验来源的 Feature Guide 草案 {feature_id} 提交人工审核",
            evidence=evidence,
            operations=[PatchOperation(
                op="upsert_generated_block", block_id=f"feature-{feature_id}",
                content=markdown_path.read_text(encoding="utf-8").rstrip(),
            )],
            confidence=0.8,
            change_range=change_range,
            dry_run=dry_run,
        )

    def get(self, proposal_id: str) -> Proposal:
        path = self._proposal_path(proposal_id)
        if not path.exists():
            raise ProposalNotFoundError(f"提案不存在：{proposal_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"提案文件损坏：{proposal_id}") from error
        validate_instance(payload, PROPOSAL_SCHEMA)
        return Proposal.from_dict(payload)

    def list(self, status: str | None = None) -> list[Proposal]:
        proposals: list[Proposal] = []
        if not self.directory.exists():
            return proposals
        for path in sorted(self.directory.glob("kp-*.json")):
            proposal = self.get(path.stem)
            if status is None or proposal.status == status:
                proposals.append(proposal)
        return proposals

    def pending_count(self) -> int:
        return len(self.list("pending"))

    def apply(
        self, proposal_id: str, *, reviewer: str, review_reason: str, dry_run: bool = False,
    ) -> dict[str, Any]:
        self._validate_review(reviewer, review_reason)
        with project_lock(self.root):
            proposal = self.get(proposal_id)
            if proposal.status == "applied":
                return {**proposal.to_dict(), "action": "apply", "idempotent": True, "dry_run": dry_run}
            if proposal.status != "pending":
                raise ProposalStateError(f"提案 {proposal_id} 当前状态为 {proposal.status}，不能应用")
            target_path, _ = self._target_path(proposal.target)
            current_hash = hash_file(target_path) if target_path.exists() else MISSING_HASH
            changed_sources = [
                path for path, expected in proposal.source_hashes.items()
                if not (self.root / path).is_file() or hash_file(self.root / path) != expected
            ]
            if current_hash != proposal.target_hash or changed_sources:
                if dry_run:
                    raise ProposalConflictError("目标或来源自提案生成后发生变化；请重新生成提案")
                proposal.status = "conflicted"
                proposal.conflict_reason = (
                    "目标内容自提案生成后发生变化；原提案已过期，必须重新生成"
                    if current_hash != proposal.target_hash
                    else "提案来源自生成后发生变化：" + "、".join(changed_sources)
                )
                proposal.reviewer = reviewer.strip()
                proposal.reviewed_at = utc_now()
                proposal.review_reason = review_reason.strip()
                self._store(proposal)
                raise ProposalConflictError(proposal.conflict_reason)

            before = read_text(target_path) if target_path.exists() else ""
            after = self._apply_operations(before, proposal, reviewer)
            result_hash = hash_text(after)
            preview = {
                **proposal.to_dict(), "action": "apply", "dry_run": dry_run,
                "would_change": before != after, "result_hash": result_hash,
                "diff": self._diff(proposal.target, before, after),
            }
            if dry_run:
                return preview
            atomic_write(target_path, after)
            proposal.status = "applied"
            proposal.reviewer = reviewer.strip()
            proposal.reviewed_at = utc_now()
            proposal.review_reason = review_reason.strip()
            proposal.result_hash = result_hash
            self._store(proposal)
            return {**proposal.to_dict(), "action": "apply", "dry_run": False, "idempotent": False}

    def reject(
        self, proposal_id: str, *, reviewer: str, review_reason: str, dry_run: bool = False,
    ) -> dict[str, Any]:
        self._validate_review(reviewer, review_reason)
        with project_lock(self.root):
            proposal = self.get(proposal_id)
            if proposal.status == "rejected":
                return {**proposal.to_dict(), "action": "reject", "idempotent": True, "dry_run": dry_run}
            if proposal.status != "pending":
                raise ProposalStateError(f"提案 {proposal_id} 当前状态为 {proposal.status}，不能拒绝")
            if dry_run:
                return {
                    **proposal.to_dict(), "action": "reject", "dry_run": True,
                    "would_set_status": "rejected",
                }
            proposal.status = "rejected"
            proposal.reviewer = reviewer.strip()
            proposal.reviewed_at = utc_now()
            proposal.review_reason = review_reason.strip()
            self._store(proposal)
            return {**proposal.to_dict(), "action": "reject", "dry_run": False, "idempotent": False}

    def enqueue_changeset(self, changeset: dict[str, Any]) -> dict[str, Any]:
        relevant = {
            "changeset_id": changeset["id"], "base_commit": changeset.get("base_commit"),
            "head_commit": changeset.get("head_commit"), "task_summary": changeset["task_summary"],
            "changed_files": changeset["changed_files"], "changed_symbols": changeset["changed_symbols"],
            "affected_knowledge": changeset["affected_knowledge"],
        }
        canonical = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        queue_id = "sq-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        payload = {
            "schema_version": 1, "queue_id": queue_id,
            "status": "awaiting_semantic_generation", "created_at": utc_now(), **relevant,
        }
        path = self.queue_directory / f"{queue_id}.json"
        if not path.exists():
            atomic_json(path, payload)
        return payload

    def queue_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if self.queue_directory.exists():
            for path in sorted(self.queue_directory.glob("sq-*.json")):
                items.append(json.loads(path.read_text(encoding="utf-8")))
        return items

    def _validate_intent(
        self, target_path: Path, target: str, reason: str, evidence: list[str],
        operations: list[PatchOperation], confidence: float,
    ) -> None:
        if not reason.strip():
            raise ValueError("提案理由不能为空")
        if not evidence:
            raise ValueError("提案必须提供至少一个来源")
        if not 0 <= confidence <= 1:
            raise ValueError("提案置信度必须位于 0 到 1")
        if not operations:
            raise ValueError("提案必须包含至少一个 Patch operation")
        is_decision = target.startswith("docs/knowledge/decisions/")
        if is_decision:
            if target_path.exists():
                raise ValueError("ADR 只允许创建新草案，不能改写已有决策")
            if len(operations) != 1 or operations[0].op != "append_adr_draft":
                raise ValueError("ADR 目标只允许 append_adr_draft operation")
        elif any(item.op == "append_adr_draft" for item in operations):
            raise ValueError("append_adr_draft 只能写入 docs/knowledge/decisions/")
        for operation in operations:
            if operation.op in {"upsert_generated_block", "delete_generated_block"}:
                if not operation.block_id or not BLOCK_ID_PATTERN.fullmatch(operation.block_id):
                    raise ValueError(f"{operation.op} 必须提供合法 block_id")
            if operation.op in {"upsert_generated_block", "append_adr_draft"} and operation.content is None:
                raise ValueError(f"{operation.op} 必须提供 content")
            if operation.op == "delete_generated_block":
                if not operation.supersedes:
                    raise ValueError("删除 generated block 必须提供 supersedes 替代证据")
                if not operation.deleted_sources:
                    raise ValueError("删除 generated block 必须提供 deleted_sources 来源删除证据")

    def _target_path(self, target: str) -> tuple[Path, str]:
        if "\\" in target:
            raise ValueError("目标必须使用项目内 POSIX 相对路径")
        pure = PurePosixPath(target)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError("目标必须位于项目目录内")
        relative = pure.as_posix()
        allowed = (
            relative.startswith("docs/knowledge/curated/")
            or relative.startswith("docs/knowledge/decisions/")
        )
        if not allowed or not relative.endswith(".md"):
            raise ValueError("提案目标必须是 curated 或 decisions 下的 Markdown")
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("目标越出项目目录")
        return resolved, relative

    def _proposal_path(self, proposal_id: str) -> Path:
        if not PROPOSAL_ID_PATTERN.fullmatch(proposal_id):
            raise ValueError(f"无效提案 ID：{proposal_id}")
        return self.directory / f"{proposal_id}.json"

    def _source_hashes(self, evidence: list[str]) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for item in evidence:
            if "\\" in item:
                continue
            path = self.root / PurePosixPath(item)
            resolved = path.resolve()
            if resolved.is_relative_to(self.root) and resolved.is_file():
                hashes[resolved.relative_to(self.root).as_posix()] = hash_file(resolved)
        return hashes

    def _link_queue(self, proposal: Proposal) -> None:
        evidence_paths = set(proposal.source_hashes)
        for item in self.queue_items():
            if item.get("status") != "awaiting_semantic_generation":
                continue
            changed_files = set(item.get("changed_files", []))
            range_values = {
                item.get("changeset_id"), item.get("head_commit"),
                f"{item.get('base_commit')}..{item.get('head_commit')}",
            }
            if not evidence_paths.intersection(changed_files) and proposal.change_range not in range_values:
                continue
            item["status"] = "proposal_created"
            item["proposal_ids"] = sorted({*item.get("proposal_ids", []), proposal.proposal_id})
            item["updated_at"] = utc_now()
            atomic_json(self.queue_directory / f"{item['queue_id']}.json", item)

    @classmethod
    def _iter_sources(cls, value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            sources = value.get("sources")
            if isinstance(sources, list):
                yield from (item for item in sources if isinstance(item, dict))
            for key, child in value.items():
                if key != "sources":
                    yield from cls._iter_sources(child)
        elif isinstance(value, list):
            for child in value:
                yield from cls._iter_sources(child)

    def _store(self, proposal: Proposal) -> None:
        payload = proposal.to_dict()
        validate_instance(payload, PROPOSAL_SCHEMA)
        atomic_json(self._proposal_path(proposal.proposal_id), payload)

    def _apply_operations(self, initial: str, proposal: Proposal, reviewer: str) -> str:
        if proposal.target.startswith("docs/knowledge/decisions/"):
            return self._render_adr_draft(proposal, reviewer)
        content = initial
        for operation in proposal.operations:
            if operation.op == "upsert_generated_block":
                content = self._upsert_block(content, operation.block_id or "", operation.content or "")
            elif operation.op == "delete_generated_block":
                content = self._delete_block(content, operation.block_id or "")
            else:
                raise ValueError(f"curated 目标不支持 operation：{operation.op}")
        return content

    @staticmethod
    def _block_pattern(block_id: str) -> re.Pattern[str]:
        start = f'<!-- project-kb:generated id="{block_id}" -->'
        end = "<!-- /project-kb:generated -->"
        return re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)

    def _upsert_block(self, current: str, block_id: str, body: str) -> str:
        start = f'<!-- project-kb:generated id="{block_id}" -->'
        block = f"{start}\n{body.rstrip()}\n<!-- /project-kb:generated -->\n"
        pattern = self._block_pattern(block_id)
        if pattern.search(current):
            return pattern.sub(block, current, count=1)
        return current.rstrip() + ("\n\n" if current.strip() else "") + block

    def _delete_block(self, current: str, block_id: str) -> str:
        pattern = self._block_pattern(block_id)
        if not pattern.search(current):
            raise ValueError(f"目标中不存在 generated block：{block_id}")
        return pattern.sub("", current, count=1).rstrip() + "\n"

    @staticmethod
    def _render_adr_draft(proposal: Proposal, reviewer: str) -> str:
        body = (proposal.operations[0].content or "").strip()
        lines = body.splitlines()
        if lines and lines[0].startswith("# "):
            title, remainder = lines[0], "\n".join(lines[1:]).strip()
        else:
            title, remainder = "# ADR 草案", body
        metadata = [
            "- 状态：草案", f"- 来源提案：{proposal.proposal_id}",
            f"- 创建审核人：{reviewer.strip()}",
        ]
        if proposal.operations[0].supersedes:
            metadata.append("- 替代：" + "、".join(proposal.operations[0].supersedes))
        return title + "\n\n" + "\n".join(metadata) + ("\n\n" + remainder if remainder else "") + "\n"

    @staticmethod
    def _validate_review(reviewer: str, reason: str) -> None:
        if not reviewer.strip():
            raise ValueError("审核人不能为空")
        if not reason.strip():
            raise ValueError("审核理由不能为空")

    @staticmethod
    def _diff(target: str, before: str, after: str) -> str:
        return "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=target, tofile=target,
        ))
