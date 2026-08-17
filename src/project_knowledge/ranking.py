"""Deterministic ranking contracts and policy-v1 candidate scoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


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
    exact_identity: int = 100
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
