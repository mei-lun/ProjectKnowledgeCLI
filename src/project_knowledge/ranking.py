"""Deterministic ranking contracts and policy-v1 candidate scoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable


STAGE_PRIORITY = {
    "direct_symbol": 4,
    "knowledge_source": 3,
    "impact": 2,
    "fallback": 1,
}


@dataclass
class FileCandidate:
    path: str
    stages: set[str] = field(default_factory=set)
    anchors: set[str] = field(default_factory=set)
    exact_symbol: bool = False
    qualified_symbol: bool = False
    exact_path: bool = False
    exact_filename: bool = False
    exact_module: bool = False
    direct_knowledge_source: bool = False
    graph_hop: int | None = None
    module: str = ""
    task_role_match: bool = False
    path_terms: set[str] = field(default_factory=set)
    symbol_terms: set[str] = field(default_factory=set)
    content_terms: set[str] = field(default_factory=set)
    is_test: bool = False
    affected_test: bool = False
    requires_live_source: bool = False
    unavailable_signals: set[str] = field(default_factory=set)
    original_order: int = 0


@dataclass(frozen=True)
class RankingPolicy:
    name: str = "policy-v1"
    exact_identity: int = 104
    qualified_identity: int = 70
    file_or_module_identity: int = 40
    direct_knowledge_source: int = 35
    graph_hop_1: int = 30
    graph_hop_2: int = 12
    task_role_match: int = 20
    path_term: int = 8
    symbol_term: int = 6
    content_term: int = 2
    irrelevant_test_penalty: int = -25
    fallback_only_penalty: int = -15
    core_min_score: int = 30
    supporting_min_score: int = 12
    core_limit: int = 5
    full_limit: int = 10


@dataclass(frozen=True)
class ScoreBreakdown:
    identity: int
    provenance: int
    relation: int
    role: int
    text: int
    penalties: int
    total: int
    reasons: tuple[str, ...]
    unavailable_signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return _as_json_dict(asdict(self))


@dataclass(frozen=True)
class RankedFile:
    path: str
    tier: str
    score: int
    score_breakdown: ScoreBreakdown
    selection_stage: str
    why_selected: str
    requires_live_source: bool = False
    protected: bool = False

    def to_dict(self) -> dict[str, object]:
        return _as_json_dict(asdict(self))


@dataclass(frozen=True)
class RankingResult:
    core_files: tuple[str, ...]
    supporting_files: tuple[str, ...]
    files: tuple[str, ...]
    file_rankings: tuple[RankedFile, ...]
    withheld_files: tuple[dict[str, str], ...]
    rejected_files: tuple[dict[str, str], ...]
    ranking_policy: str
    ranking_status: str
    ranking_confidence: str
    reason_code: str | None = None
    protected_candidates_truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return _as_json_dict(asdict(self))


def _as_json_dict(value: object) -> dict[str, object]:
    converted = _json_compatible(value)
    assert isinstance(converted, dict)
    return converted


def _json_compatible(value: object) -> object:
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    return value


DEFAULT_RANKING_POLICY = RankingPolicy()


def rank_files(
    candidates: Iterable[FileCandidate],
    *,
    allowed_paths: set[str],
    policy: RankingPolicy = DEFAULT_RANKING_POLICY,
) -> RankingResult:
    merged, rejected = _normalize_and_merge(candidates, allowed_paths)
    ranked = [
        _to_ranked(candidate, score_candidate(candidate, policy))
        for candidate in merged
    ]
    ranked.sort(key=lambda item: (-item.score, -STAGE_PRIORITY[item.selection_stage], item.path))
    eligible_core = [item for item in ranked if item.score >= policy.core_min_score]
    core = eligible_core[: min(policy.core_limit, policy.full_limit)]
    confidence = "high"
    if not core and ranked:
        core = ranked[:1]
        confidence = "low"
    core_paths = {item.path for item in core}
    remaining = [item for item in ranked if item.path not in core_paths]
    protected = [item for item in remaining if item.protected]
    ordinary = [
        item for item in remaining
        if not item.protected and item.score >= policy.supporting_min_score
    ]
    supporting = (protected + ordinary)[: max(0, policy.full_limit - len(core))]
    selected_paths = {item.path for item in core + supporting}
    withheld = _withheld_rows(ranked, selected_paths, policy)
    return RankingResult(
        core_files=tuple(item.path for item in core),
        supporting_files=tuple(item.path for item in supporting),
        files=tuple(item.path for item in core + supporting),
        file_rankings=tuple(
            _with_tier(item, "core" if item.path in core_paths else "supporting")
            for item in core + supporting
        ),
        withheld_files=tuple(withheld),
        rejected_files=tuple(rejected),
        ranking_policy=policy.name,
        ranking_status="ok",
        ranking_confidence=confidence,
        protected_candidates_truncated=len([item for item in ranked if item.protected]) > policy.full_limit,
    )


def fallback_rank_files(
    candidates: Iterable[FileCandidate],
    *,
    allowed_paths: set[str],
    reason_code: str,
    policy: RankingPolicy = DEFAULT_RANKING_POLICY,
) -> RankingResult:
    merged, rejected = _normalize_and_merge(candidates, allowed_paths)
    original_orders = {candidate.path: candidate.original_order for candidate in merged}
    ranked = [
        _to_ranked(candidate, score_candidate(candidate, policy))
        for candidate in merged
    ]
    ranked.sort(key=lambda item: (original_orders[item.path], item.path))
    core = ranked[: min(policy.core_limit, policy.full_limit)]
    supporting = ranked[policy.core_limit : policy.full_limit]
    core_paths = {item.path for item in core}
    selected = core + supporting
    selected_paths = {item.path for item in selected}
    return RankingResult(
        core_files=tuple(item.path for item in core),
        supporting_files=tuple(item.path for item in supporting),
        files=tuple(item.path for item in selected),
        file_rankings=tuple(
            _with_tier(item, "core" if item.path in core_paths else "supporting")
            for item in selected
        ),
        withheld_files=tuple(_withheld_rows(ranked, selected_paths, policy)),
        rejected_files=tuple(rejected),
        ranking_policy=policy.name,
        ranking_status="fallback",
        ranking_confidence="low",
        reason_code=reason_code,
        protected_candidates_truncated=len([item for item in ranked if item.protected]) > policy.full_limit,
    )


def _normalize_and_merge(
    candidates: Iterable[FileCandidate], allowed_paths: set[str]
) -> tuple[list[FileCandidate], list[dict[str, str]]]:
    normalized_allowed = {_normalize_path(path) for path in allowed_paths}
    valid_allowed = {path for path in normalized_allowed if path is not None}
    merged: dict[str, FileCandidate] = {}
    rejected: list[dict[str, str]] = []
    for candidate in candidates:
        path = _normalize_path(candidate.path)
        if path is None or path not in valid_allowed:
            rejected.append({"path": candidate.path, "reason_code": "path_not_allowed"})
            continue
        normalized = _copy_candidate(candidate, path=path)
        existing = merged.get(path)
        merged[path] = normalized if existing is None else _merge_candidates(existing, normalized)
    return list(merged.values()), rejected


def _normalize_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/")[0]:
        return None
    if any(part == ".." for part in normalized.split("/")):
        return None
    return normalized


def _copy_candidate(candidate: FileCandidate, *, path: str) -> FileCandidate:
    return FileCandidate(
        path=path,
        stages=set(candidate.stages),
        anchors=set(candidate.anchors),
        exact_symbol=candidate.exact_symbol,
        qualified_symbol=candidate.qualified_symbol,
        exact_path=candidate.exact_path,
        exact_filename=candidate.exact_filename,
        exact_module=candidate.exact_module,
        direct_knowledge_source=candidate.direct_knowledge_source,
        graph_hop=candidate.graph_hop,
        module=candidate.module,
        task_role_match=candidate.task_role_match,
        path_terms=set(candidate.path_terms),
        symbol_terms=set(candidate.symbol_terms),
        content_terms=set(candidate.content_terms),
        is_test=candidate.is_test,
        affected_test=candidate.affected_test,
        requires_live_source=candidate.requires_live_source,
        unavailable_signals=set(candidate.unavailable_signals),
        original_order=candidate.original_order,
    )


def _merge_candidates(left: FileCandidate, right: FileCandidate) -> FileCandidate:
    graph_hops = [hop for hop in (left.graph_hop, right.graph_hop) if hop is not None]
    return FileCandidate(
        path=left.path,
        stages=left.stages | right.stages,
        anchors=left.anchors | right.anchors,
        exact_symbol=left.exact_symbol or right.exact_symbol,
        qualified_symbol=left.qualified_symbol or right.qualified_symbol,
        exact_path=left.exact_path or right.exact_path,
        exact_filename=left.exact_filename or right.exact_filename,
        exact_module=left.exact_module or right.exact_module,
        direct_knowledge_source=left.direct_knowledge_source or right.direct_knowledge_source,
        graph_hop=min(graph_hops) if graph_hops else None,
        module=left.module or right.module,
        task_role_match=left.task_role_match or right.task_role_match,
        path_terms=left.path_terms | right.path_terms,
        symbol_terms=left.symbol_terms | right.symbol_terms,
        content_terms=left.content_terms | right.content_terms,
        is_test=left.is_test or right.is_test,
        affected_test=left.affected_test or right.affected_test,
        requires_live_source=left.requires_live_source or right.requires_live_source,
        unavailable_signals=left.unavailable_signals | right.unavailable_signals,
        original_order=min(left.original_order, right.original_order),
    )


def _to_ranked(candidate: FileCandidate, breakdown: ScoreBreakdown) -> RankedFile:
    selection_stage = max(
        (stage for stage in candidate.stages if stage in STAGE_PRIORITY),
        key=STAGE_PRIORITY.__getitem__,
        default="fallback",
    )
    return RankedFile(
        path=candidate.path,
        tier="",
        score=breakdown.total,
        score_breakdown=breakdown,
        selection_stage=selection_stage,
        why_selected=",".join(breakdown.reasons),
        requires_live_source=candidate.requires_live_source,
        protected=(
            candidate.exact_symbol
            or candidate.exact_path
            or candidate.direct_knowledge_source
            or candidate.graph_hop == 1
        ),
    )


def _with_tier(item: RankedFile, tier: str) -> RankedFile:
    return RankedFile(
        path=item.path,
        tier=tier,
        score=item.score,
        score_breakdown=item.score_breakdown,
        selection_stage=item.selection_stage,
        why_selected=item.why_selected,
        requires_live_source=item.requires_live_source,
        protected=item.protected,
    )


def _withheld_rows(
    ranked: list[RankedFile], selected_paths: set[str], policy: RankingPolicy
) -> list[dict[str, str]]:
    return [
        {
            "path": item.path,
            "reason_code": (
                "below_supporting_threshold"
                if not item.protected and item.score < policy.supporting_min_score
                else "selection_limit"
            ),
        }
        for item in ranked
        if item.path not in selected_paths
    ]


def score_candidate(candidate: FileCandidate, policy: RankingPolicy) -> ScoreBreakdown:
    identity = (
        policy.exact_identity
        if candidate.exact_symbol or candidate.exact_path
        else policy.qualified_identity
        if candidate.qualified_symbol
        else policy.file_or_module_identity
        if candidate.exact_filename or candidate.exact_module
        else 0
    )
    provenance = policy.direct_knowledge_source if candidate.direct_knowledge_source else 0
    relation = (
        policy.graph_hop_1
        if candidate.graph_hop == 1
        else policy.graph_hop_2
        if candidate.graph_hop == 2
        else 0
    )
    role = policy.task_role_match if candidate.task_role_match else 0
    text = (
        min(3, len(candidate.path_terms)) * policy.path_term
        + min(3, len(candidate.symbol_terms)) * policy.symbol_term
        + min(4, len(candidate.content_terms)) * policy.content_term
    )
    protected_test = (
        candidate.exact_symbol
        or candidate.direct_knowledge_source
        or candidate.affected_test
        or candidate.graph_hop == 1
    )
    penalties = 0
    if candidate.is_test and not candidate.task_role_match and not protected_test:
        penalties += policy.irrelevant_test_penalty
    if candidate.stages == {"fallback"} and not (identity or provenance or relation):
        penalties += policy.fallback_only_penalty
    total = identity + provenance + relation + role + text + penalties
    return ScoreBreakdown(
        identity=identity,
        provenance=provenance,
        relation=relation,
        role=role,
        text=text,
        penalties=penalties,
        total=total,
        reasons=_score_reasons(candidate, identity, provenance, relation, role, text, penalties),
        unavailable_signals=tuple(sorted(candidate.unavailable_signals)),
    )


def _score_reasons(
    candidate: FileCandidate,
    identity: int,
    provenance: int,
    relation: int,
    role: int,
    text: int,
    penalties: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if identity:
        if candidate.exact_symbol or candidate.exact_path:
            reasons.append("exact_identity")
        elif candidate.qualified_symbol:
            reasons.append("qualified_identity")
        else:
            reasons.append("file_or_module_identity")
    if provenance:
        reasons.append("direct_knowledge_source")
    if relation:
        reasons.append("graph_hop_1" if candidate.graph_hop == 1 else "graph_hop_2")
    if role:
        reasons.append("task_role_match")
    if text:
        if candidate.path_terms:
            reasons.append("path_terms")
        if candidate.symbol_terms:
            reasons.append("symbol_terms")
        if candidate.content_terms:
            reasons.append("content_terms")
    if penalties:
        protected_test = (
            candidate.exact_symbol
            or candidate.direct_knowledge_source
            or candidate.affected_test
            or candidate.graph_hop == 1
        )
        if candidate.is_test and not candidate.task_role_match and not protected_test:
            reasons.append("irrelevant_test")
        if candidate.stages == {"fallback"} and not (identity or provenance or relation):
            reasons.append("fallback_only")
    return tuple(reasons)
