from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any, ClassVar, Literal


RunStatus = Literal[
    "scanning",
    "category_review",
    "categories_confirmed",
    "guidance_generation",
    "guidance_review",
    "complete",
    "failed",
]
BatchStatus = Literal["pending", "completed", "failed"]
DraftKind = Literal["category_catalog", "methodology", "guidance"]
DraftStatus = Literal[
    "incomplete", "awaiting_confirmation", "confirmed", "rejected"
]
UpdateLevel = Literal["fact", "guidance", "category"]
TaskGenerationStatus = Literal["pending", "generated", "skipped", "failed"]


def _require_id(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")


def _require_optional_id(name: str, value: str | None) -> None:
    if value is not None:
        _require_id(name, value)


def _require_iso_time(name: str, value: str | None) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是 ISO-8601 时间字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} 必须是 ISO-8601 时间字符串") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} 必须包含时区")


def _require_int(name: str, value: object, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} 必须是大于等于 {minimum} 的整数")


def _require_number(name: str, value: object, minimum: float, maximum: float) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} 必须是 {minimum} 到 {maximum} 之间的有限数字")


def _require_choice(name: str, value: str, choices: set[str]) -> None:
    if value not in choices:
        raise ValueError(f"{name} 的值 {value!r} 无效")


@dataclass(slots=True)
class GuidanceRun:
    run_id: str
    project_root: str
    snapshot_id: str
    status: RunStatus
    total_files: int
    covered_files: int
    created_at: str
    updated_at: str
    uncovered_files: list[str] = field(default_factory=list)
    error: str | None = None

    _STATUSES: ClassVar[set[str]] = {
        "scanning", "category_review", "categories_confirmed",
        "guidance_generation", "guidance_review", "complete", "failed",
    }

    def __post_init__(self) -> None:
        _require_id("run_id", self.run_id)
        _require_id("snapshot_id", self.snapshot_id)
        _require_choice("status", self.status, self._STATUSES)
        _require_iso_time("created_at", self.created_at)
        _require_iso_time("updated_at", self.updated_at)
        _require_int("total_files", self.total_files)
        _require_int("covered_files", self.covered_files)
        if self.covered_files > self.total_files:
            raise ValueError("covered_files 不能大于 total_files")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GuidanceRun":
        return cls(
            run_id=value["run_id"], project_root=value["project_root"],
            snapshot_id=value["snapshot_id"], status=value["status"],
            total_files=value["total_files"], covered_files=value["covered_files"],
            created_at=value["created_at"], updated_at=value["updated_at"],
            uncovered_files=list(value.get("uncovered_files", [])), error=value.get("error"),
        )


@dataclass(slots=True)
class GuidanceBatch:
    batch_id: str
    run_id: str
    ordinal: int
    status: BatchStatus
    files: list[str]
    snapshot_id: str
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error: str | None = None

    _STATUSES: ClassVar[set[str]] = {"pending", "completed", "failed"}

    def __post_init__(self) -> None:
        _require_id("batch_id", self.batch_id)
        _require_id("run_id", self.run_id)
        _require_id("snapshot_id", self.snapshot_id)
        _require_choice("status", self.status, self._STATUSES)
        _require_iso_time("created_at", self.created_at)
        _require_iso_time("updated_at", self.updated_at)
        _require_int("ordinal", self.ordinal)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GuidanceBatch":
        return cls(
            batch_id=value["batch_id"], run_id=value["run_id"],
            ordinal=value["ordinal"], status=value["status"],
            files=list(value.get("files", [])), snapshot_id=value["snapshot_id"],
            created_at=value["created_at"], updated_at=value["updated_at"],
            result=dict(value["result"]) if value.get("result") is not None else None,
            error=value.get("error"),
        )


@dataclass(slots=True)
class GuidanceCategory:
    category_id: str
    run_id: str
    name: str
    purpose: str
    applies_to: list[str]
    excludes: list[str]
    samples: list[str]
    evidence: list[dict[str, Any]]
    confidence: float
    unknowns: list[Any]
    created_at: str
    updated_at: str
    relations: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_id("category_id", self.category_id)
        _require_id("run_id", self.run_id)
        if not self.name.strip():
            raise ValueError("name 不能为空")
        _require_number("confidence", self.confidence, 0, 1)
        _require_iso_time("created_at", self.created_at)
        _require_iso_time("updated_at", self.updated_at)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GuidanceCategory":
        return cls(
            category_id=value["category_id"], run_id=value["run_id"], name=value["name"],
            purpose=value["purpose"], applies_to=list(value.get("applies_to", [])),
            excludes=list(value.get("excludes", [])), samples=list(value.get("samples", [])),
            evidence=[dict(item) for item in value.get("evidence", [])],
            confidence=value["confidence"], unknowns=list(value.get("unknowns", [])),
            created_at=value["created_at"], updated_at=value["updated_at"],
            relations=[dict(item) for item in value.get("relations", [])],
        )


@dataclass(slots=True)
class GuidanceDraft:
    draft_id: str
    run_id: str
    kind: DraftKind
    status: DraftStatus
    path: str
    content_hash: str
    snapshot_id: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str
    category_id: str | None = None
    rejection_reason: str | None = None
    confirmed_at: str | None = None

    _KINDS: ClassVar[set[str]] = {"category_catalog", "methodology", "guidance"}
    _STATUSES: ClassVar[set[str]] = {
        "incomplete", "awaiting_confirmation", "confirmed", "rejected",
    }

    def __post_init__(self) -> None:
        _require_id("draft_id", self.draft_id)
        _require_id("run_id", self.run_id)
        _require_id("snapshot_id", self.snapshot_id)
        _require_choice("kind", self.kind, self._KINDS)
        _require_choice("status", self.status, self._STATUSES)
        _require_optional_id("category_id", self.category_id)
        _require_iso_time("created_at", self.created_at)
        _require_iso_time("updated_at", self.updated_at)
        _require_iso_time("confirmed_at", self.confirmed_at)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GuidanceDraft":
        return cls(
            draft_id=value["draft_id"], run_id=value["run_id"], kind=value["kind"],
            status=value["status"], path=value["path"], content_hash=value["content_hash"],
            snapshot_id=value["snapshot_id"], payload=dict(value.get("payload", {})),
            created_at=value["created_at"], updated_at=value["updated_at"],
            category_id=value.get("category_id"), rejection_reason=value.get("rejection_reason"),
            confirmed_at=value.get("confirmed_at"),
        )


@dataclass(slots=True)
class GuidanceVersion:
    version_id: str
    category_id: str
    version: int
    title: str
    content: str
    content_hash: str
    snapshot_id: str
    evidence: list[dict[str, Any]]
    is_current: bool
    created_at: str
    draft_id: str | None = None
    asset_type: Literal["methodology", "project_guidance"] = "project_guidance"

    def __post_init__(self) -> None:
        _require_id("version_id", self.version_id)
        _require_id("category_id", self.category_id)
        _require_id("snapshot_id", self.snapshot_id)
        _require_optional_id("draft_id", self.draft_id)
        _require_choice("asset_type", self.asset_type, {"methodology", "project_guidance"})
        _require_iso_time("created_at", self.created_at)
        if not isinstance(self.is_current, bool):
            raise ValueError("is_current 必须是布尔值")
        _require_int("version", self.version, minimum=1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GuidanceVersion":
        return cls(
            version_id=value["version_id"], category_id=value["category_id"],
            version=value["version"], title=value["title"], content=value["content"],
            content_hash=value["content_hash"], snapshot_id=value["snapshot_id"],
            evidence=[dict(item) for item in value.get("evidence", [])],
            is_current=value["is_current"], created_at=value["created_at"],
            draft_id=value.get("draft_id"), asset_type=value.get("asset_type", "project_guidance"),
        )


@dataclass(slots=True)
class GuidanceChange:
    change_id: str
    project_root: str
    base_snapshot_id: str
    head_snapshot_id: str
    update_level: UpdateLevel
    changed_files: list[str]
    affected_categories: list[str]
    payload: dict[str, Any]
    created_at: str
    processed_at: str | None = None

    _LEVELS: ClassVar[set[str]] = {"fact", "guidance", "category"}

    def __post_init__(self) -> None:
        _require_id("change_id", self.change_id)
        _require_id("project_root", self.project_root)
        _require_id("base_snapshot_id", self.base_snapshot_id)
        _require_id("head_snapshot_id", self.head_snapshot_id)
        _require_choice("update_level", self.update_level, self._LEVELS)
        _require_iso_time("created_at", self.created_at)
        _require_iso_time("processed_at", self.processed_at)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GuidanceChange":
        return cls(
            change_id=value["change_id"], project_root=value["project_root"],
            base_snapshot_id=value["base_snapshot_id"],
            head_snapshot_id=value["head_snapshot_id"], update_level=value["update_level"],
            changed_files=list(value.get("changed_files", [])),
            affected_categories=list(value.get("affected_categories", [])),
            payload=dict(value.get("payload", {})), created_at=value["created_at"],
            processed_at=value.get("processed_at"),
        )


@dataclass(slots=True)
class TaskCompletion:
    task_id: str
    project_root: str
    summary: str
    changed_files: list[str]
    changed_symbols: list[str]
    tests: list[dict[str, Any]]
    base_snapshot_id: str
    final_snapshot_id: str
    user_confirmed: bool
    generation_status: TaskGenerationStatus
    affected_categories: list[str]
    created_at: str
    updated_at: str
    skip_reason: str | None = None
    error: str | None = None

    _STATUSES: ClassVar[set[str]] = {"pending", "generated", "skipped", "failed"}

    def __post_init__(self) -> None:
        _require_id("task_id", self.task_id)
        _require_id("project_root", self.project_root)
        _require_id("summary", self.summary)
        _require_id("base_snapshot_id", self.base_snapshot_id)
        _require_id("final_snapshot_id", self.final_snapshot_id)
        _require_choice("generation_status", self.generation_status, self._STATUSES)
        _require_iso_time("created_at", self.created_at)
        _require_iso_time("updated_at", self.updated_at)
        if not isinstance(self.user_confirmed, bool):
            raise ValueError("user_confirmed 必须是布尔值")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskCompletion":
        return cls(
            task_id=value["task_id"], project_root=value["project_root"],
            summary=value["summary"], changed_files=list(value.get("changed_files", [])),
            changed_symbols=list(value.get("changed_symbols", [])),
            tests=[dict(item) for item in value.get("tests", [])],
            base_snapshot_id=value["base_snapshot_id"], final_snapshot_id=value["final_snapshot_id"],
            user_confirmed=bool(value.get("user_confirmed", False)),
            generation_status=value["generation_status"],
            affected_categories=list(value.get("affected_categories", [])),
            created_at=value["created_at"], updated_at=value["updated_at"],
            skip_reason=value.get("skip_reason"), error=value.get("error"),
        )
