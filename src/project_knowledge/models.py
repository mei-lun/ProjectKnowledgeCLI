from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Confidence = Literal["verified", "generated", "inferred"]
Freshness = Literal["fresh", "potentially_stale", "stale", "conflicted"]


@dataclass(slots=True)
class SourceReference:
    type: Literal["file", "symbol", "commit", "config", "task", "decision"]
    path: str | None = None
    id: str | None = None
    line: int | None = None
    hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(slots=True)
class KnowledgeRecord:
    id: str
    kind: str
    title: str
    path: str
    ownership: Literal["generated", "draft", "curated", "decision"]
    confidence: Confidence
    status: Freshness = "fresh"
    sources: list[SourceReference] = field(default_factory=list)
    source_commit: str | None = None
    source_hashes: dict[str, str] = field(default_factory=dict)
    last_generated_at: str | None = None
    last_verified_at: str | None = None
    supersedes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    content: str = ""

    def to_dict(self, include_content: bool = False) -> dict[str, Any]:
        result = asdict(self)
        result["sources"] = [source.to_dict() for source in self.sources]
        if not include_content:
            result.pop("content", None)
        for key in ["source_commit", "last_generated_at", "last_verified_at"]:
            if result.get(key) is None:
                result.pop(key, None)
        for key in ["source_hashes", "supersedes", "tags"]:
            if not result.get(key):
                result.pop(key, None)
        return result


@dataclass(slots=True)
class ChangeSet:
    id: str
    base_commit: str | None
    head_commit: str | None
    task_summary: str
    changed_files: list[str] = field(default_factory=list)
    changed_symbols: list[str] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    affected_knowledge: list[str] = field(default_factory=list)
    tests_run: list[str] = field(default_factory=list)
    test_results: list[str] = field(default_factory=list)
    author: str = "ai-or-user"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PatchOperation:
    op: Literal["upsert_generated_block", "delete_generated_block", "append_adr_draft"]
    content: str | None = None
    block_id: str | None = None
    supersedes: list[str] = field(default_factory=list)
    deleted_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if result["content"] is None:
            result.pop("content")
        if result["block_id"] is None:
            result.pop("block_id")
        if not result["supersedes"]:
            result.pop("supersedes")
        if not result["deleted_sources"]:
            result.pop("deleted_sources")
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PatchOperation":
        return cls(
            op=value["op"], content=value.get("content"), block_id=value.get("block_id"),
            supersedes=list(value.get("supersedes", [])),
            deleted_sources=list(value.get("deleted_sources", [])),
        )


@dataclass(slots=True)
class Proposal:
    proposal_id: str
    target: str
    target_hash: str
    reason: str
    evidence: list[str]
    source_hashes: dict[str, str]
    confidence: float
    operations: list[PatchOperation]
    created_at: str
    schema_version: int = 1
    change_range: str | None = None
    requires_review: bool = True
    status: Literal["pending", "applied", "rejected", "conflicted"] = "pending"
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_reason: str | None = None
    result_hash: str | None = None
    conflict_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["operations"] = [operation.to_dict() for operation in self.operations]
        for key in [
            "change_range", "reviewer", "reviewed_at", "review_reason",
            "result_hash", "conflict_reason",
        ]:
            if result[key] is None:
                result.pop(key)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Proposal":
        return cls(
            proposal_id=value["proposal_id"], target=value["target"], target_hash=value["target_hash"],
            reason=value["reason"], evidence=list(value["evidence"]),
            source_hashes=dict(value.get("source_hashes", {})), confidence=float(value["confidence"]),
            operations=[PatchOperation.from_dict(item) for item in value["operations"]],
            created_at=value["created_at"], schema_version=int(value.get("schema_version", 1)),
            change_range=value.get("change_range"), requires_review=bool(value.get("requires_review", True)),
            status=value.get("status", "pending"), reviewer=value.get("reviewer"),
            reviewed_at=value.get("reviewed_at"), review_reason=value.get("review_reason"),
            result_hash=value.get("result_hash"), conflict_reason=value.get("conflict_reason"),
        )


@dataclass(slots=True)
class SecretRedaction:
    kind: str
    line: int
    replacement: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OmittedEvidence:
    path: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceItem:
    kind: Literal["file", "symbol", "knowledge", "config", "relation"]
    path: str
    content: str
    content_hash: str
    tokens: int
    redactions: list[SecretRedaction] = field(default_factory=list)
    symbol_id: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["redactions"] = [item.to_dict() for item in self.redactions]
        for key in ["symbol_id", "start_line", "end_line"]:
            if result[key] is None:
                result.pop(key)
        return result


@dataclass(slots=True)
class EvidencePack:
    task: str
    items: list[EvidenceItem]
    omitted: list[OmittedEvidence]
    files_considered: int
    files_included: int
    estimated_tokens: int
    pack_hash: str
    source_commit: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "task": self.task,
            "items": [item.to_dict() for item in self.items],
            "omitted": [item.to_dict() for item in self.omitted],
            "files_considered": self.files_considered,
            "files_included": self.files_included,
            "estimated_tokens": self.estimated_tokens,
            "pack_hash": self.pack_hash,
        }
        if self.source_commit is not None:
            result["source_commit"] = self.source_commit
        return result


@dataclass(slots=True)
class Symbol:
    id: str
    name: str
    kind: str
    path: str
    line: int
    end_line: int | None = None
    signature: str = ""
    source_hash: str = ""
    confidence: float = 1.0


@dataclass(slots=True)
class Relation:
    source: str
    target: str
    kind: str
    path: str
    line: int | None = None
    confidence: float = 1.0
    resolved: bool = False


