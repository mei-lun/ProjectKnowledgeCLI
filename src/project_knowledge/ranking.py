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
    channels: set[str] = field(default_factory=set)
    anchors: set[str] = field(default_factory=set)
    exact_symbol: bool = False
    qualified_symbol: bool = False
    exact_path: bool = False
    exact_filename: bool = False
    exact_module: bool = False
    definition_match: bool = False
    specific_symbol: bool = False
    generic_symbol: bool = False
    direct_knowledge_source: bool = False
    verified_knowledge: bool = False
    graph_hop: int | None = None
    module: str = ""
    task_role_match: bool = False
    query_role_match: bool = False
    path_terms: set[str] = field(default_factory=set)
    symbol_terms: set[str] = field(default_factory=set)
    content_terms: set[str] = field(default_factory=set)
    is_test: bool = False
    affected_test: bool = False
    is_generated: bool = False
    is_vendor: bool = False
    high_degree_hub: bool = False
    auxiliary_source: bool = False
    requires_live_source: bool = False
    unavailable_signals: set[str] = field(default_factory=set)
    original_order: int = 0


@dataclass(frozen=True)
class RankingPolicy:
    name: str = "policy-v2"
    exact_identity: int = 104
    qualified_identity: int = 70
    file_or_module_identity: int = 40
    direct_knowledge_source: int = 35
    graph_hop_1: int = 30
    graph_hop_2: int = 12
    task_role_match: int = 0
    query_role_match: int = 80
    definition_match: int = 24
    specific_symbol_match: int = 10
    verified_knowledge: int = 12
    channel_consensus: int = 4
    requested_test_boost: int = 24
    path_term: int = 8
    symbol_term: int = 6
    content_term: int = 2
    irrelevant_test_penalty: int = -25
    fallback_only_penalty: int = -15
    generic_symbol_penalty: int = -70
    vendor_penalty: int = -65
    generated_penalty: int = -18
    high_degree_hub_penalty: int = -10
    profile_mismatch_penalty: int = -35
    generic_test_penalty: int = -40
    auxiliary_source_penalty: int = -25
    core_min_score: int = 30
    supporting_min_score: int = 12
    core_limit: int = 5
    full_limit: int = 10
    optional_limit: int = 5


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
    optional_files: tuple[str, ...] = ()

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
LEGACY_RANKING_POLICY = RankingPolicy(
    name="policy-v1",
    task_role_match=20,
    query_role_match=0,
    definition_match=0,
    specific_symbol_match=0,
    verified_knowledge=0,
    channel_consensus=0,
    requested_test_boost=0,
    generic_symbol_penalty=0,
    vendor_penalty=0,
    generated_penalty=0,
    high_degree_hub_penalty=0,
    profile_mismatch_penalty=0,
    generic_test_penalty=0,
    auxiliary_source_penalty=0,
)


def rank_files(
    candidates: Iterable[FileCandidate],
    *,
    allowed_paths: set[str],
    policy: RankingPolicy = DEFAULT_RANKING_POLICY,
    query_type: str = "investigation",
) -> RankingResult:
    merged, rejected = _normalize_and_merge(candidates, allowed_paths)
    ranked = [
        _to_ranked(candidate, score_candidate(candidate, policy, query_type=query_type))
        for candidate in merged
    ]
    ranked.sort(key=lambda item: (-item.score, -STAGE_PRIORITY[item.selection_stage], item.path))
    eligible_core = [item for item in ranked if item.score >= policy.core_min_score]
    core = eligible_core[: min(policy.core_limit, policy.full_limit)]
    if query_type == "test_config" and core:
        test_paths = {candidate.path for candidate in merged if candidate.is_test}
        core = _ensure_test_source_diversity(core, eligible_core, test_paths)
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
    optional = [
        item for item in remaining
        if item.path not in {candidate.path for candidate in supporting}
        and item.selection_stage != "fallback"
    ][: max(0, policy.optional_limit)]
    selected_paths = {item.path for item in core + supporting + optional}
    core_supporting_paths = core_paths | {item.path for item in supporting}
    withheld = _withheld_rows(ranked, selected_paths, policy)
    return RankingResult(
        core_files=tuple(item.path for item in core),
        supporting_files=tuple(item.path for item in supporting),
        files=tuple(item.path for item in core + supporting),
        file_rankings=tuple(
            _with_tier(
                item,
                "core" if item.path in core_paths
                else "supporting" if item.path in core_supporting_paths
                else "optional",
            )
            for item in core + supporting + optional
        ),
        withheld_files=tuple(withheld),
        rejected_files=tuple(rejected),
        ranking_policy=policy.name,
        ranking_status="ok",
        ranking_confidence=confidence,
        optional_files=tuple(item.path for item in optional),
        protected_candidates_truncated=len([item for item in ranked if item.protected]) > policy.full_limit,
    )


def fallback_rank_files(
    candidates: Iterable[FileCandidate],
    *,
    allowed_paths: set[str],
    reason_code: str,
    policy: RankingPolicy = DEFAULT_RANKING_POLICY,
    query_type: str = "investigation",
) -> RankingResult:
    merged, rejected = _normalize_and_merge(candidates, allowed_paths)
    original_orders = {candidate.path: candidate.original_order for candidate in merged}
    ranked = [
        _to_ranked(candidate, score_candidate(candidate, policy, query_type=query_type))
        for candidate in merged
    ]
    ranked.sort(key=lambda item: (original_orders[item.path], item.path))
    core_limit = min(policy.core_limit, policy.full_limit)
    core = ranked[:core_limit]
    supporting = ranked[core_limit : policy.full_limit]
    optional = ranked[policy.full_limit : policy.full_limit + max(0, policy.optional_limit)]
    core_paths = {item.path for item in core}
    selected = core + supporting
    selected_paths = {item.path for item in selected + optional}
    return RankingResult(
        core_files=tuple(item.path for item in core),
        supporting_files=tuple(item.path for item in supporting),
        files=tuple(item.path for item in selected),
        file_rankings=tuple(
            _with_tier(
                item,
                "core" if item.path in core_paths
                else "supporting" if item.path in {candidate.path for candidate in supporting}
                else "optional",
            )
            for item in selected + optional
        ),
        withheld_files=tuple(_withheld_rows(ranked, selected_paths, policy)),
        rejected_files=tuple(rejected),
        ranking_policy=policy.name,
        ranking_status="fallback",
        ranking_confidence="low",
        reason_code=reason_code,
        optional_files=tuple(item.path for item in optional),
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
        channels=set(candidate.channels),
        anchors=set(candidate.anchors),
        exact_symbol=candidate.exact_symbol,
        qualified_symbol=candidate.qualified_symbol,
        exact_path=candidate.exact_path,
        exact_filename=candidate.exact_filename,
        exact_module=candidate.exact_module,
        definition_match=candidate.definition_match,
        specific_symbol=candidate.specific_symbol,
        generic_symbol=candidate.generic_symbol,
        direct_knowledge_source=candidate.direct_knowledge_source,
        verified_knowledge=candidate.verified_knowledge,
        graph_hop=candidate.graph_hop,
        module=candidate.module,
        task_role_match=candidate.task_role_match,
        query_role_match=candidate.query_role_match,
        path_terms=set(candidate.path_terms),
        symbol_terms=set(candidate.symbol_terms),
        content_terms=set(candidate.content_terms),
        is_test=candidate.is_test,
        affected_test=candidate.affected_test,
        is_generated=candidate.is_generated,
        is_vendor=candidate.is_vendor,
        high_degree_hub=candidate.high_degree_hub,
        auxiliary_source=candidate.auxiliary_source,
        requires_live_source=candidate.requires_live_source,
        unavailable_signals=set(candidate.unavailable_signals),
        original_order=candidate.original_order,
    )


def _merge_candidates(left: FileCandidate, right: FileCandidate) -> FileCandidate:
    graph_hops = [hop for hop in (left.graph_hop, right.graph_hop) if hop is not None]
    return FileCandidate(
        path=left.path,
        stages=left.stages | right.stages,
        channels=left.channels | right.channels,
        anchors=left.anchors | right.anchors,
        exact_symbol=left.exact_symbol or right.exact_symbol,
        qualified_symbol=left.qualified_symbol or right.qualified_symbol,
        exact_path=left.exact_path or right.exact_path,
        exact_filename=left.exact_filename or right.exact_filename,
        exact_module=left.exact_module or right.exact_module,
        definition_match=left.definition_match or right.definition_match,
        specific_symbol=left.specific_symbol or right.specific_symbol,
        generic_symbol=left.generic_symbol or right.generic_symbol,
        direct_knowledge_source=left.direct_knowledge_source or right.direct_knowledge_source,
        verified_knowledge=left.verified_knowledge or right.verified_knowledge,
        graph_hop=min(graph_hops) if graph_hops else None,
        module=left.module or right.module,
        task_role_match=left.task_role_match or right.task_role_match,
        query_role_match=left.query_role_match or right.query_role_match,
        path_terms=left.path_terms | right.path_terms,
        symbol_terms=left.symbol_terms | right.symbol_terms,
        content_terms=left.content_terms | right.content_terms,
        is_test=left.is_test or right.is_test,
        affected_test=left.affected_test or right.affected_test,
        is_generated=left.is_generated or right.is_generated,
        is_vendor=left.is_vendor or right.is_vendor,
        high_degree_hub=left.high_degree_hub or right.high_degree_hub,
        auxiliary_source=left.auxiliary_source or right.auxiliary_source,
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
            (candidate.exact_symbol and (candidate.specific_symbol or not candidate.generic_symbol))
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


def score_candidate(
    candidate: FileCandidate,
    policy: RankingPolicy,
    *,
    query_type: str = "investigation",
) -> ScoreBreakdown:
    identity_base = (
        policy.exact_identity
        if candidate.exact_symbol or candidate.exact_path
        else policy.qualified_identity
        if candidate.qualified_symbol
        else policy.file_or_module_identity
        if candidate.exact_filename or candidate.exact_module
        else 0
    )
    identity = (
        identity_base
        + (policy.definition_match if candidate.definition_match else 0)
        + (policy.specific_symbol_match if candidate.specific_symbol else 0)
    )
    provenance = (
        (policy.direct_knowledge_source if candidate.direct_knowledge_source else 0)
        + (policy.verified_knowledge if candidate.verified_knowledge else 0)
        + max(0, min(3, len(candidate.channels) - 1)) * policy.channel_consensus
    )
    relation = (
        policy.graph_hop_1
        if candidate.graph_hop == 1
        else policy.graph_hop_2
        if candidate.graph_hop == 2
        else 0
    )
    role = (
        (policy.task_role_match if candidate.task_role_match else 0)
        + (policy.query_role_match if candidate.query_role_match else 0)
        + (
            policy.requested_test_boost
            if query_type == "test_config" and candidate.is_test and candidate.query_role_match
            else 0
        )
    )
    text = (
        min(3, len(candidate.path_terms)) * policy.path_term
        + min(3, len(candidate.symbol_terms)) * policy.symbol_term
        + min(4, len(candidate.content_terms)) * policy.content_term
    )
    protected_test = (
        candidate.direct_knowledge_source
        or candidate.affected_test
        or candidate.graph_hop == 1
    )
    penalties = 0
    if candidate.is_test and not candidate.task_role_match and not protected_test:
        penalties += policy.irrelevant_test_penalty
    if candidate.stages == {"fallback"} and not (identity or provenance or relation):
        penalties += policy.fallback_only_penalty
    if candidate.generic_symbol and not candidate.specific_symbol:
        penalties += policy.generic_symbol_penalty
    if candidate.is_vendor:
        penalties += policy.vendor_penalty
    if candidate.is_generated:
        penalties += policy.generated_penalty
    if candidate.high_degree_hub:
        penalties += policy.high_degree_hub_penalty
    if (
        query_type in {"extension_point", "configuration", "design_reason"}
        and not candidate.query_role_match
        and (candidate.exact_symbol or candidate.specific_symbol or candidate.definition_match)
    ):
        penalties += policy.profile_mismatch_penalty
    if query_type == "test_config" and candidate.is_test and not candidate.query_role_match:
        penalties += policy.generic_test_penalty
    if candidate.auxiliary_source:
        penalties += policy.auxiliary_source_penalty
    total = identity + provenance + relation + role + text + penalties
    return ScoreBreakdown(
        identity=identity,
        provenance=provenance,
        relation=relation,
        role=role,
        text=text,
        penalties=penalties,
        total=total,
        reasons=_score_reasons(
            candidate,
            identity,
            provenance,
            relation,
            role,
            text,
            penalties,
            query_type=query_type,
        ),
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
    *,
    query_type: str = "investigation",
) -> tuple[str, ...]:
    reasons: list[str] = []
    if candidate.exact_symbol or candidate.exact_path:
        reasons.append("exact_identity")
    elif candidate.qualified_symbol:
        reasons.append("qualified_identity")
    elif candidate.exact_filename or candidate.exact_module:
        reasons.append("file_or_module_identity")
    if candidate.definition_match:
        reasons.append("symbol_definition")
    if candidate.specific_symbol:
        reasons.append("specific_symbol")
    if provenance:
        if candidate.direct_knowledge_source:
            reasons.append("direct_knowledge_source")
        if candidate.verified_knowledge:
            reasons.append("verified_knowledge")
        if len(candidate.channels) > 1:
            reasons.append("channel_consensus")
    if relation:
        reasons.append("graph_hop_1" if candidate.graph_hop == 1 else "graph_hop_2")
    if role:
        if candidate.task_role_match:
            reasons.append("task_role_match")
        if candidate.query_role_match:
            reasons.append("query_role_match")
        if query_type == "test_config" and candidate.is_test and candidate.query_role_match:
            reasons.append("requested_test")
    if text:
        if candidate.path_terms:
            reasons.append("path_terms")
        if candidate.symbol_terms:
            reasons.append("symbol_terms")
        if candidate.content_terms:
            reasons.append("content_terms")
    if penalties:
        protected_test = (
            candidate.direct_knowledge_source
            or candidate.affected_test
            or candidate.graph_hop == 1
        )
        if candidate.is_test and not candidate.task_role_match and not protected_test:
            reasons.append("irrelevant_test")
        if candidate.stages == {"fallback"} and not (identity or provenance or relation):
            reasons.append("fallback_only")
        if candidate.generic_symbol and not candidate.specific_symbol:
            reasons.append("generic_symbol")
        if candidate.is_vendor:
            reasons.append("vendor_source")
        if candidate.is_generated:
            reasons.append("generated_source")
        if candidate.high_degree_hub:
            reasons.append("high_degree_hub")
        if (
            query_type in {"extension_point", "configuration", "design_reason"}
            and not candidate.query_role_match
            and (candidate.exact_symbol or candidate.specific_symbol or candidate.definition_match)
        ):
            reasons.append("query_profile_mismatch")
        if query_type == "test_config" and candidate.is_test and not candidate.query_role_match:
            reasons.append("generic_test_noise")
        if candidate.auxiliary_source:
            reasons.append("auxiliary_source")
    return tuple(reasons)


def _ensure_test_source_diversity(
    core: list[RankedFile],
    eligible: list[RankedFile],
    test_paths: set[str],
) -> list[RankedFile]:
    """Keep at least one test and one source in Core for explicit test queries."""
    has_test = any(item.path in test_paths for item in core)
    has_source = any(item.path not in test_paths for item in core)
    replacement: RankedFile | None = None
    replace_test = False
    if not has_test:
        replacement = next((item for item in eligible if item.path in test_paths), None)
    elif not has_source:
        replacement = next((item for item in eligible if item.path not in test_paths), None)
        replace_test = True
    if replacement is None or replacement in core:
        return core
    replace_index = next(
        (
            index
            for index in range(len(core) - 1, -1, -1)
            if (core[index].path in test_paths) == replace_test
        ),
        len(core) - 1,
    )
    diversified = list(core)
    diversified[replace_index] = replacement
    order = {item.path: index for index, item in enumerate(eligible)}
    diversified.sort(key=lambda item: order[item.path])
    return diversified
