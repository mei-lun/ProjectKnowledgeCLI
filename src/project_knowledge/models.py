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
    ownership: Literal["generated", "curated", "decision"]
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
        return {key: value for key, value in result.items() if value not in (None, [], {})}


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
class Proposal:
    proposal_id: str
    target: str
    reason: str
    evidence: list[str]
    confidence: float
    operations: list[str]
    requires_review: bool = True
    status: Literal["pending", "applied", "rejected"] = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


@dataclass(slots=True)
class Route:
    method: str
    route: str
    handler: str
    path: str
    line: int


@dataclass(slots=True)
class ParseResult:
    symbols: list[Symbol] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    parse_error: str | None = None
    parser: str = "generic"

