# WP-12A Retrieval Precision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, explainable file reranker that returns high-precision core evidence while preserving high-recall supporting evidence and enforcing the new WP-12A quality gates.

**Architecture:** Candidate generation remains in the existing retrieval and baseline strategy paths. A new production `ranking.py` module owns normalization, multi-signal scoring, stable ordering, core/supporting partitioning, and structured fallback; `KnowledgeAPI.context()` and every evaluation strategy consume that shared contract. Evaluation adds strict core metrics, ranking diagnostics, hard-negative samples, and release gates without changing the existing 40 answers.

**Tech Stack:** Python 3.11+, standard-library dataclasses/path handling/statistics, unittest/pytest, JSONL evaluation data, SQLite-backed Project Knowledge API, optional CodeGraph 1.5 adapter.

**Spec:** `docs/superpowers/specs/2026-08-17-wp12a-retrieval-precision-design.md`

## Global Constraints

- Work package: `WP-12A`; primary requirement `RT-010`; supporting requirements `RT-006`, `RT-007`, `RT-008`.
- Use the local Project Knowledge System at task start and call knowledge impact before cross-module edits.
- Prefix every shell command with `rtk`.
- Keep the current 40 `expected_files` answers byte-for-byte unchanged; add at least 10 hard-negative samples.
- Do not add network, Embedding, LLM reranking, remote Provider, or project-level ranking weight configuration.
- Keep CodeGraph and builtin fact provenance distinct; `adapter_unavailable` must remain explicit.
- Preserve all existing recall, symbol, call-path, extension-point, invariant, design-reason, success, Token, tool-call, and latency gates.
- New hard gates: hybrid core recall >= 0.85, hybrid core precision >= 0.40, hybrid full recall >= 0.94, hybrid full precision >= 0.22, hybrid average returned files <= 10, hybrid average context <= 1000 Token, code precision >= 0.25, Markdown precision >= 0.30, grep precision >= 0.32, and ranking fallback rate = 0.
- The implementation batch bumps the patch version exactly once with `python scripts/bump_version.py "提高检索精确率并增加核心证据重排"`.
- Do not mark RT-010 complete until implementation, positive/negative tests, evaluation, docs, version, curated review, generated knowledge, and finalization all pass.
- All manual repository edits use `apply_patch`; generated formatting and version scripts may write their documented outputs.

---

## File Map

- Create `src/project_knowledge/ranking.py`: candidate contract, policy-v1, normalization, scoring, stable ordering, partitioning, explanation, and fallback result.
- Create `tests/test_ranking.py`: focused unit tests for every score class, merge rule, partition boundary, determinism, filtering, and fallback.
- Modify `src/project_knowledge/retrieval.py`: construct production candidates, invoke the reranker, publish additive context fields, and trim supporting evidence before core evidence.
- Modify `tests/test_retrieval_wp06.py`: context compatibility, file tiers, Token behavior, stale filtering, builtin behavior, and ranking fallback tests.
- Modify `src/project_knowledge/evaluate.py`: reuse production rankings, adapt Markdown/grep candidate generation, calculate core/ranking metrics, and expose ranking failures.
- Modify `tests/test_evaluate.py`: dataset schema, shared-ranker strategy behavior, strict core metrics, nDCG, aggregate counts, and quality-gate failures.
- Modify `evaluation/questions.jsonl`: append 10 reviewed hard-negative questions without changing the first 40 records.
- Modify `evaluation/thresholds.json`: require 50 samples and the approved 0.1.29 precision/ranking limits.
- Modify `.github/workflows/quality.yml`: point the quick gate to `self-repo-0.1.29.json` only after the clean baseline is frozen.
- Modify `docs/project-knowledge-system-audit.md`: record WP-12A evidence and update RT-010 only after all gates pass.
- Modify `docs/knowledge/curated/architecture.md`, `docs/knowledge/curated/conventions.md`, and `docs/knowledge/curated/feature-guide-generation.md`: document the verified production ranking boundary, gates, and context fields.
- Modify `src/project_knowledge/__init__.py`, `CHANGELOG.md`, and `plugins/project-knowledge/.codex-plugin/plugin.json` through the version bump script.
- Generate/update `evaluation/reports/latest.json`, `evaluation/baselines/self-repo-0.1.29.json`, `.project-kb/manifest.json`, and affected generated knowledge during release finalization.

---

### Task 1: Ranking Contracts and Policy-v1 Scoring

**Files:**
- Create: `src/project_knowledge/ranking.py`
- Create: `tests/test_ranking.py`

**Interfaces:**
- Consumes: normalized project-relative path strings and evidence features supplied by retrieval callers.
- Produces: `FileCandidate`, `RankingPolicy`, `ScoreBreakdown`, `RankedFile`, `RankingResult`, `DEFAULT_RANKING_POLICY`, and `score_candidate(candidate, policy) -> ScoreBreakdown`.

- [ ] **Step 1: Write the failing score-contract tests**

Create `tests/test_ranking.py` with a `RankingTests(unittest.TestCase)` fixture and these exact assertions:

```python
from __future__ import annotations

import unittest

from project_knowledge.ranking import (
    DEFAULT_RANKING_POLICY,
    FileCandidate,
    score_candidate,
)


class RankingTests(unittest.TestCase):
    def test_policy_v1_scores_each_category_once(self) -> None:
        candidate = FileCandidate(
            path="src/app.py",
            stages={"direct_symbol", "knowledge_source", "impact"},
            anchors={"src/app.py::AccountService.login"},
            exact_symbol=True,
            qualified_symbol=True,
            exact_filename=True,
            direct_knowledge_source=True,
            graph_hop=1,
            task_role_match=True,
            path_terms={"account", "login", "service", "ignored"},
            symbol_terms={"account", "login", "service", "ignored"},
            content_terms={"account", "login", "service", "repo", "extra"},
        )

        breakdown = score_candidate(candidate, DEFAULT_RANKING_POLICY)

        self.assertEqual(breakdown.identity, 100)
        self.assertEqual(breakdown.provenance, 35)
        self.assertEqual(breakdown.relation, 30)
        self.assertEqual(breakdown.role, 20)
        self.assertEqual(breakdown.text, 50)
        self.assertEqual(breakdown.penalties, 0)
        self.assertEqual(breakdown.total, 235)

    def test_irrelevant_test_and_fallback_only_penalties_are_explicit(self) -> None:
        candidate = FileCandidate(
            path="tests/test_unrelated.py",
            stages={"fallback"},
            is_test=True,
            path_terms={"login"},
            content_terms={"login"},
        )

        breakdown = score_candidate(candidate, DEFAULT_RANKING_POLICY)

        self.assertEqual(breakdown.penalties, -40)
        self.assertIn("irrelevant_test", breakdown.reasons)
        self.assertIn("fallback_only", breakdown.reasons)


if __name__ == "__main__":
    unittest.main()
```

The text score is `min(3, len(path_terms)) * 8 + min(3, len(symbol_terms)) * 6 + min(4, len(content_terms)) * 2`, which yields `24 + 18 + 8 = 50`.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_ranking.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'project_knowledge.ranking'`.

- [ ] **Step 3: Implement the typed contracts and exact policy constants**

Add immutable dataclasses in `src/project_knowledge/ranking.py` with these public shapes:

```python
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
```

Define the remaining immutable result contracts exactly as follows; their `to_dict()` methods convert tuples and nested dataclasses into JSON-compatible lists/dicts without renaming fields:

```python
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
```

`score_candidate()` must implement the exact values in the spec, apply identity only once, use only the shortest graph hop, cap text terms at 3/3/4, exempt direct/protected affected tests from the irrelevant-test penalty, and never alter score for `requires_live_source`.

Use this calculation shape, expanding the reason labels for every non-zero component:

```python
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
```

Define `_score_reasons()` with the fixed labels `exact_identity`, `qualified_identity`, `file_or_module_identity`, `direct_knowledge_source`, `graph_hop_1`, `graph_hop_2`, `task_role_match`, `path_terms`, `symbol_terms`, `content_terms`, `irrelevant_test`, and `fallback_only`. Emit labels in that category order and only when the corresponding component contributed; do not include raw task text or exception text.

Add one assertion case with `unavailable_signals={"graph", "symbol"}` and require `ScoreBreakdown.unavailable_signals == ("graph", "symbol")`; sorting the names is part of determinism.

- [ ] **Step 4: Run score tests and verify GREEN**

Run:

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_ranking.py -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
rtk git add -- src/project_knowledge/ranking.py tests/test_ranking.py
rtk git commit -m "feat: add deterministic ranking policy"
```

---

### Task 2: Candidate Merge, Stable Partitioning, and Structured Fallback

**Files:**
- Modify: `src/project_knowledge/ranking.py`
- Modify: `tests/test_ranking.py`

**Interfaces:**
- Consumes: `Iterable[FileCandidate]`, `allowed_paths: set[str]`, and an optional `RankingPolicy`.
- Produces: `rank_files(candidates, *, allowed_paths, policy=DEFAULT_RANKING_POLICY) -> RankingResult` and `fallback_rank_files(candidates, *, allowed_paths, reason_code, policy=DEFAULT_RANKING_POLICY) -> RankingResult`.

- [ ] **Step 1: Add failing merge, ordering, tier, and fallback tests**

Append tests that prove:

```python
def test_rank_files_merges_duplicate_evidence_and_is_stable(self) -> None:
    candidates = [
        FileCandidate(path="src/z.py", stages={"fallback"}, content_terms={"login"}, original_order=0),
        FileCandidate(path="src/app.py", stages={"impact"}, graph_hop=1, original_order=1),
        FileCandidate(
            path="src/app.py",
            stages={"direct_symbol"},
            anchors={"src/app.py::login"},
            exact_symbol=True,
            original_order=2,
        ),
        FileCandidate(path="../outside.py", stages={"direct_symbol"}, exact_symbol=True, original_order=3),
    ]

    result = rank_files(candidates, allowed_paths={"src/app.py", "src/z.py"})

    self.assertEqual(result.core_files, ("src/app.py",))
    self.assertEqual(result.supporting_files, ())
    self.assertEqual(result.files, ("src/app.py",))
    self.assertEqual(result.file_rankings[0].score_breakdown.identity, 100)
    self.assertEqual(result.file_rankings[0].score_breakdown.relation, 30)
    self.assertEqual(result.file_rankings[0].selection_stage, "direct_symbol")
    self.assertEqual(result.rejected_files[0]["reason_code"], "path_not_allowed")

def test_rank_files_caps_core_and_preserves_only_qualified_supporting(self) -> None:
    candidates = [
        FileCandidate(path=f"src/core_{index}.py", exact_symbol=True, stages={"direct_symbol"}, original_order=index)
        for index in range(6)
    ]
    candidates.extend([
        FileCandidate(path="src/support.py", graph_hop=2, stages={"impact"}, original_order=6),
        FileCandidate(path="src/weak.py", content_terms={"x"}, stages={"fallback"}, original_order=7),
    ])
    allowed = {candidate.path for candidate in candidates}

    result = rank_files(candidates, allowed_paths=allowed)

    self.assertEqual(len(result.core_files), 5)
    self.assertIn("src/core_5.py", result.supporting_files)
    self.assertIn("src/support.py", result.supporting_files)
    self.assertNotIn("src/weak.py", result.files)
    self.assertEqual(result.withheld_files[-1]["reason_code"], "below_supporting_threshold")

def test_fallback_preserves_original_order_and_reports_reason(self) -> None:
    candidates = [
        FileCandidate(path="src/b.py", original_order=0),
        FileCandidate(path="src/a.py", original_order=1),
    ]

    result = fallback_rank_files(
        candidates,
        allowed_paths={"src/a.py", "src/b.py"},
        reason_code="ranking_error",
    )

    self.assertEqual(result.files, ("src/b.py", "src/a.py"))
    self.assertEqual(result.ranking_status, "fallback")
    self.assertEqual(result.reason_code, "ranking_error")
```

- [ ] **Step 2: Run the focused tests and verify RED**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_ranking.py -q
```

Expected: imports or assertions fail because `rank_files()` and `fallback_rank_files()` do not exist.

- [ ] **Step 3: Implement normalization, merge, selection, and explanations**

Implement these rules exactly:

- normalize `\` to `/`, remove leading `./`, reject empty/absolute/parent-traversal paths, and require membership in `allowed_paths`;
- merge duplicate candidates by unioning `stages`, `anchors`, and term sets; OR boolean evidence; keep the shortest non-null `graph_hop` and smallest `original_order`;
- sort by `(-score, -stage_priority, path)` with `direct_symbol=4`, `knowledge_source=3`, `impact=2`, `fallback=1`;
- core is the first five candidates scoring at least 30; if none qualify, keep the highest candidate as one low-confidence core result;
- supporting includes subsequent candidates scoring at least 12 until full size 10;
- exact identity, direct knowledge source, and one-hop relation candidates are protected through normal supporting selection, but the full result never exceeds 10; set `protected_candidates_truncated` when more than 10 protected candidates exist;
- `why_selected` is composed only from deterministic reason labels and does not contain absolute paths or exception text;
- fallback uses normalized original order, first five as core and next five as supporting, with `ranking_status="fallback"`.

Declare the tie-break map once in the module so merging, conversion, and sorting use the same source of truth:

```python
STAGE_PRIORITY = {
    "direct_symbol": 4,
    "knowledge_source": 3,
    "impact": 2,
    "fallback": 1,
}
```

The main selection must follow this structure so later callers receive one stable contract:

```python
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
    core = eligible_core[: policy.core_limit]
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
        file_rankings=tuple(_with_tier(item, "core" if item.path in core_paths else "supporting") for item in core + supporting),
        withheld_files=tuple(withheld),
        rejected_files=tuple(rejected),
        ranking_policy=policy.name,
        ranking_status="ok",
        ranking_confidence=confidence,
        protected_candidates_truncated=len([item for item in ranked if item.protected]) > policy.full_limit,
    )
```

`_normalize_and_merge`, `_to_ranked`, `_withheld_rows`, and `_with_tier` are private helpers in the same module. `_to_ranked` sets `protected=True` for exact identity, direct knowledge source, or one-hop relation and selects the highest stage from `STAGE_PRIORITY`.

- [ ] **Step 4: Run the complete ranking unit suite**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_ranking.py -q
```

Expected: all ranking tests pass on repeated runs with identical ordering.

- [ ] **Step 5: Commit Task 2**

```powershell
rtk git add -- src/project_knowledge/ranking.py tests/test_ranking.py
rtk git commit -m "feat: partition ranked file evidence"
```

---

### Task 3: Integrate Ranking into KnowledgeAPI.context

**Files:**
- Modify: `src/project_knowledge/retrieval.py:295-408`
- Modify: `src/project_knowledge/retrieval.py:648-690`
- Modify: `tests/test_retrieval_wp06.py`

**Interfaces:**
- Consumes: `rank_files(...)`, `fallback_rank_files(...)`, current symbol matches, `impact` response, selected knowledge fragments, and engine discovery paths.
- Produces: additive `core_files`, `supporting_files`, `files`, `file_rankings`, `withheld_files`, `ranking_policy`, `ranking_status`, `ranking_confidence`, and `protected_candidates_truncated` fields on `KnowledgeAPI.context()`.

- [ ] **Step 1: Add failing context contract and noise tests**

Add these tests to `RetrievalWP06Tests`:

```python
def test_context_returns_ranked_core_and_supporting_files(self) -> None:
    result = self.api.context("新增类似功能 create_item", max_tokens=1200)

    self.assertEqual(result["ranking_status"], "ok")
    self.assertEqual(result["ranking_policy"], "policy-v1")
    self.assertLessEqual(len(result["core_files"]), 5)
    self.assertLessEqual(len(result["files"]), 10)
    self.assertEqual(
        result["files"],
        result["core_files"] + result["supporting_files"],
    )
    self.assertEqual(result["core_files"][0], "src/app.py")
    self.assertEqual(
        [item["path"] for item in result["file_rankings"]],
        result["files"],
    )
    self.assertTrue(all(item["why_selected"] for item in result["file_rankings"]))

def test_unrelated_test_file_does_not_displace_exact_source(self) -> None:
    (self.root / "tests" / "test_noise.py").write_text(
        "def create_item():\n    return 'noise'\n" * 50,
        encoding="utf-8",
    )
    ProjectService(self.root).sync()

    result = KnowledgeAPI(self.root).context("create_item", max_tokens=1200)

    self.assertEqual(result["core_files"][0], "src/app.py")
    self.assertNotEqual(result["core_files"][0], "tests/test_noise.py")

def test_context_ranking_failure_is_structured_and_compatible(self) -> None:
    with patch("project_knowledge.retrieval.rank_files", side_effect=RuntimeError("private C:\\secret")):
        result = self.api.context("create_item", max_tokens=1200)

    self.assertEqual(result["ranking_status"], "fallback")
    self.assertEqual(result["ranking_reason_code"], "ranking_error")
    self.assertNotIn("secret", json.dumps(result, ensure_ascii=False))
    self.assertIn("symbols", result)
    self.assertIn("knowledge", result)
    self.assertIn("impact", result)
```

Add `json` and `patch` imports to the test file.

- [ ] **Step 2: Run retrieval tests and verify RED**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_retrieval_wp06.py -q
```

Expected: the new context fields are absent.

- [ ] **Step 3: Add production candidate construction and ranker invocation**

In `retrieval.py`:

- import the ranking contracts/functions;
- add `_context_file_candidates(task, intent, symbol_matches, impact, fragments) -> tuple[list[FileCandidate], set[str]]`;
- build `allowed_paths` from `engine.discover(root, config)` and normalize them to POSIX paths;
- add direct-symbol candidates from `symbol_matches`, impact candidates from `dependency_files`, `affected_files`, and `affected_tests`, knowledge-source candidates from fresh fragments, and fallback candidates only when task terms intersect a path/symbol/content excerpt;
- set `graph_hop` from `impact["relations"]` when available; use hop 1 for explicit affected tests returned directly by the engine;
- set `unavailable_signals={"graph"}` only when the active engine reports no graph/impact capability; an empty result from an available capability is not “unavailable”;
- identify exact/prefix/path/module matches from normalized task terms without reading evaluation labels;
- call `rank_files`; catch `Exception` only at this boundary and call `fallback_rank_files(reason_code="ranking_error")` without serializing the exception;
- merge `RankingResult.to_dict()` into the result before `_fit_context()`.

Use a single exception boundary; do not place try/except inside score calculation:

```python
candidates, allowed_paths = self._context_file_candidates(
    task, intent, symbol_matches, impact, fragments
)
try:
    ranked_files = rank_files(candidates, allowed_paths=allowed_paths)
except Exception:
    ranked_files = fallback_rank_files(
        candidates,
        allowed_paths=allowed_paths,
        reason_code="ranking_error",
    )
result.update(ranked_files.to_dict())
result["ranking_reason_code"] = ranked_files.reason_code
```

- [ ] **Step 4: Make Token fitting remove supporting evidence first**

Update `_fit_context()` so each size-reduction iteration performs this order before trimming legacy fields:

1. pop the last `supporting_files` entry;
2. remove the same path from `files` and `file_rankings`;
3. append `{"path": path, "reason_code": "token_budget"}` to `withheld_files`;
4. never remove a core file;
5. continue the existing knowledge/symbol/gap trimming behavior.

Recompute `estimated_tokens` after every removal as the existing function does.

- [ ] **Step 5: Run retrieval, integration, and CodeGraph fixture tests**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_retrieval_wp06.py tests/test_integration.py tests/test_codegraph_validation.py -q
```

Expected: all tests pass; the CodeGraph context test still reports `fact_source="codegraph"` and ranks `src/app.lua`/`src/router.lua` without SQLite symbols.

- [ ] **Step 6: Commit Task 3**

```powershell
rtk git add -- src/project_knowledge/retrieval.py tests/test_retrieval_wp06.py
rtk git commit -m "feat: rank context file evidence"
```

---

### Task 4: Make Every Evaluation Strategy Use the Production Reranker

**Files:**
- Modify: `src/project_knowledge/evaluate.py:375-621`
- Modify: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: `KnowledgeAPI.context()` rankings for hybrid/code/codegraph and `rank_files()` for Markdown/grep candidates.
- Produces: `_retrieve(...)` with ordered `core_files`, `files`, `file_rankings`, `ranking_status`, symbols, call path, text, and selection reasons for every available strategy.

- [ ] **Step 1: Replace evaluator-private ranking tests with shared-ranker assertions**

Remove imports/tests for `_rank_markdown_source_paths`, `_select_grep_files`, and `_novel_ranked_paths`. Add tests that patch `project_knowledge.evaluate.rank_files` and assert it is called for Markdown and grep, while hybrid consumes context ordering:

```python
def test_all_available_strategies_return_ranking_contract(self) -> None:
    api = KnowledgeAPI(self.root)
    sample = load_dataset(self.dataset)[0]

    for strategy in ("hybrid", "code", "markdown", "grep_read"):
        result = _retrieve(api, sample, strategy)
        self.assertIn("core_files", result)
        self.assertIn("file_rankings", result)
        self.assertEqual(result["files"], list(dict.fromkeys(result["core_files"] + result["supporting_files"])))
        self.assertEqual(result["ranking_status"], "ok")

def test_markdown_and_grep_delegate_ordering_to_production_ranker(self) -> None:
    api = KnowledgeAPI(self.root)
    sample = load_dataset(self.dataset)[0]
    real_rank = rank_files
    calls = []

    def record_rank(candidates, **kwargs):
        calls.append([candidate.path for candidate in candidates])
        return real_rank(candidates, **kwargs)

    with patch("project_knowledge.evaluate.rank_files", side_effect=record_rank):
        _retrieve(api, sample, "markdown")
        _retrieve(api, sample, "grep_read")

    self.assertEqual(len(calls), 2)
    self.assertTrue(all(candidates for candidates in calls))
```

Import `rank_files` from `project_knowledge.ranking` in the test.

- [ ] **Step 2: Run evaluator tests and verify RED**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_evaluate.py -q
```

Expected: the current `_retrieve()` does not return the ranking contract and private helper imports still exist.

- [ ] **Step 3: Refactor `_retrieve()` without duplicating scoring**

- hybrid/codegraph: take ordered `core_files`, `supporting_files`, `files`, `file_rankings`, and ranking status from `api.context()`;
- code: preserve the context order but remove rankings whose sole/primary `selection_stage` is `knowledge_source`; rebuild core/supporting lists from the remaining ordered entries without recalculating scores, then omit knowledge text;
- Markdown: keep knowledge search/page reading as candidate generation, convert each source path to `FileCandidate(stages={"knowledge_source"})`, attach direct-source/path/content features, and call `rank_files()`;
- grep: keep raw text counting only to discover paths with at least one task-term occurrence, convert those paths to fallback candidates with `content_terms`, and call `rank_files()`; do not sort by occurrence count outside the reranker;
- remove `_path_relevance`, `_rank_markdown_source_paths`, `_select_grep_files`, and `_novel_ranked_paths` after their callers/tests are gone;
- build `selection_reasons` from `file_rankings`, not a strategy-specific dict;
- retain the current Token caps for Markdown and grep text payloads.

- [ ] **Step 4: Run evaluator and retrieval suites**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_evaluate.py tests/test_retrieval_wp06.py -q
```

Expected: all tests pass, and no evaluator-private file scoring helper remains.

- [ ] **Step 5: Commit Task 4**

```powershell
rtk git add -- src/project_knowledge/evaluate.py tests/test_evaluate.py
rtk git commit -m "refactor: share production file reranking"
```

---

### Task 5: Add Strict Core Metrics, nDCG, Counts, and Fallback Gate

**Files:**
- Modify: `src/project_knowledge/evaluate.py:20-80`
- Modify: `src/project_knowledge/evaluate.py:318-370`
- Modify: `src/project_knowledge/evaluate.py:624-641`
- Modify: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: `expected_files`, optional `acceptable_supporting_files`, ordered `core_files`, ordered full `files`, and `ranking_status`.
- Produces: per-sample and aggregate `core_file_precision`, `core_file_recall`, `ndcg_at_5`, `average_core_files`, `average_returned_files`, `ranking_fallback_rate`, and auxiliary `acceptable_supporting_precision` when labels exist.

- [ ] **Step 1: Add failing dataset and metric tests**

Add `acceptable_supporting_files` to the dataset validation contract as an optional non-empty-string list, but not to `EXPECTED_LIST_FIELDS`. Add these tests:

```python
from project_knowledge.evaluate import _evaluate_sample


def test_core_metrics_are_strict_and_supporting_labels_are_diagnostic(self) -> None:
    api = KnowledgeAPI(self.root)
    sample = load_dataset(self.dataset)[0]
    sample["acceptable_supporting_files"] = ["tests/test_app.py"]
    returned = {
        "files": ["src/app.py", "tests/test_app.py", "README.md"],
        "core_files": ["src/app.py", "README.md"],
        "supporting_files": ["tests/test_app.py"],
        "symbols": {"src/app.py::AccountService.login"},
        "call_path": set(sample["expected_call_path"]),
        "text": "",
        "tool_calls": 1,
        "stale_detected": False,
        "selection_reasons": {},
        "file_rankings": [],
        "ranking_status": "ok",
    }

    with patch("project_knowledge.evaluate._retrieve", return_value=returned):
        result = _evaluate_sample(api, sample, "hybrid")

    self.assertEqual(result["metrics"]["core_file_recall"], 1.0)
    self.assertEqual(result["metrics"]["core_file_precision"], 0.5)
    self.assertEqual(result["metrics"]["file_precision"], 0.333333)
    self.assertEqual(result["metrics"]["acceptable_supporting_precision"], 1.0)
    self.assertGreater(result["metrics"]["ndcg_at_5"], 0.0)

def test_ranking_fallback_is_a_sample_failure(self) -> None:
    report = {
        "strategies": {
            "hybrid": {
                "available": True,
                "samples": 50,
                "metrics": {"ranking_fallback_rate": 0.02},
            }
        }
    }
    thresholds = {
        "minimum_samples": 50,
        "required_strategies": ["hybrid"],
        "maximum": {"ranking_fallback_rate": 0.0},
    }

    gate = evaluate_quality_gate(report, thresholds)

    self.assertFalse(gate["passed"])
    self.assertTrue(any(item["metric"] == "ranking_fallback_rate" for item in gate["failures"]))
```

- [ ] **Step 2: Run metric tests and verify RED**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_evaluate.py -q
```

Expected: core and ranking metrics are missing.

- [ ] **Step 3: Implement metrics and aggregation**

Implement binary nDCG with:

```python
def _ndcg_at_k(ranked: list[str], relevant: set[str], k: int = 5) -> float:
    if not relevant:
        return 0.0
    dcg = sum(
        (1.0 / math.log2(index + 2)) if path in relevant else 0.0
        for index, path in enumerate(ranked[:k])
    )
    ideal_hits = min(k, len(relevant))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / ideal if ideal else 0.0
```

In `_evaluate_sample()`:

- compute core precision/recall against strict `expected_files`;
- record core misses in a separate `core_failed_metrics` list; do not add them to the existing `failed_metrics` or change full-evidence `success`, because an expected file found in supporting evidence is still an end-to-end retrieval success;
- keep existing full file precision/recall semantics;
- compute diagnostic `acceptable_supporting_precision` only when labels exist, using `len(set(supporting_files) & set(acceptable_supporting_files)) / len(set(supporting_files))`, with `0.0` for an empty supporting list;
- set the per-sample metric `ranking_fallback` to `1.0` when status is fallback and `0.0` otherwise; on fallback append `ranking_status` to `failed_metrics`;
- include ordered core/supporting files, file rankings, and ranking status in the sample report.

In `_aggregate()` add arithmetic means for actual core count, full file count, and the per-sample `ranking_fallback` metric. Name them exactly `average_core_files`, `average_returned_files`, and `ranking_fallback_rate`.

- [ ] **Step 4: Run evaluation tests and verify GREEN**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_evaluate.py -q
```

Expected: all existing and new evaluation tests pass.

- [ ] **Step 5: Commit Task 5**

```powershell
rtk git add -- src/project_knowledge/evaluate.py tests/test_evaluate.py
rtk git commit -m "feat: measure ranked core evidence"
```

---

### Task 6: Add Hard-Negative Samples and Freeze Absolute WP-12A Gates

**Files:**
- Modify: `evaluation/questions.jsonl`
- Modify: `evaluation/thresholds.json`
- Modify: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: production files/symbols added by Tasks 1-5.
- Produces: a 50-sample schema-v1 dataset and absolute thresholds for the approved precision targets.

- [ ] **Step 1: Protect the original 40 answers before appending samples**

Add a regression test that reads the first 40 JSONL records and compares their IDs and `expected_files` to a literal tuple captured from the current 0.1.28 dataset. Store the literal as `ORIGINAL_EXPECTED_FILES` in `tests/test_evaluate.py`; include all 40 `(id, tuple(expected_files))` entries, not a hash generated at test runtime.

```python
ORIGINAL_EXPECTED_FILES = (
    ("self-init-flow", ("src/project_knowledge/service.py", "src/project_knowledge/knowledge.py", "src/project_knowledge/store.py")),
    ("self-incremental-sync", ("src/project_knowledge/service.py", "src/project_knowledge/engine.py", "src/project_knowledge/knowledge.py")),
    ("self-atomic-rebuild", ("src/project_knowledge/service.py", "src/project_knowledge/store.py")),
    ("self-commit-alignment", ("src/project_knowledge/service.py", "src/project_knowledge/util.py")),
    ("self-template-confidence", ("src/project_knowledge/knowledge.py", "src/project_knowledge/models.py")),
    ("self-module-truncation", ("src/project_knowledge/knowledge.py",)),
    ("self-runtime-schema", ("src/project_knowledge/schemas.py", "src/project_knowledge/knowledge.py", "src/project_knowledge/service.py")),
    ("self-config-capabilities", ("src/project_knowledge/config.py", "src/project_knowledge/service.py")),
    ("self-codegraph-failure", ("src/project_knowledge/codegraph.py", "docs/knowledge/decisions/0002-codegraph-adapter-boundary.md")),
    ("self-python-parser", ("src/project_knowledge/engine.py", "src/project_knowledge/models.py")),
    ("self-generic-parser", ("src/project_knowledge/engine.py", "src/project_knowledge/models.py")),
    ("self-context-budget", ("src/project_knowledge/retrieval.py", "src/project_knowledge/store.py")),
    ("self-stale-shield", ("src/project_knowledge/retrieval.py", "src/project_knowledge/service.py")),
    ("self-impact-analysis", ("src/project_knowledge/retrieval.py", "src/project_knowledge/store.py")),
    ("self-mcp-dispatch", ("src/project_knowledge/mcp.py", "src/project_knowledge/retrieval.py")),
    ("self-manifest-publication", ("src/project_knowledge/knowledge.py", "src/project_knowledge/models.py", "src/project_knowledge/schemas.py")),
    ("self-version-bump", ("src/project_knowledge/versioning.py", "src/project_knowledge/__init__.py", "CHANGELOG.md")),
    ("self-owned-integration", ("src/project_knowledge/service.py", "src/project_knowledge/util.py")),
    ("self-evaluation-gate", ("src/project_knowledge/evaluate.py", "src/project_knowledge/cli.py")),
    ("self-performance-harness", ("src/project_knowledge/performance.py", "evaluation/performance_harness.py")),
    ("self-evidence-pack", ("src/project_knowledge/evidence.py", "src/project_knowledge/models.py", "src/project_knowledge/schemas.py")),
    ("self-provider-authorization", ("src/project_knowledge/provider.py", "src/project_knowledge/config.py")),
    ("self-provider-runtime", ("src/project_knowledge/provider.py", "src/project_knowledge/schemas.py", "src/project_knowledge/util.py")),
    ("self-provider-preview", ("src/project_knowledge/cli.py", "src/project_knowledge/evidence.py", "src/project_knowledge/provider.py")),
    ("self-provider-extension", ("src/project_knowledge/provider.py",)),
    ("self-feature-guide-schema", ("src/project_knowledge/schemas.py", "src/project_knowledge/semantic.py")),
    ("self-semantic-generation", ("src/project_knowledge/semantic.py", "src/project_knowledge/provider.py", "src/project_knowledge/knowledge.py")),
    ("self-feature-source-validation", ("src/project_knowledge/semantic.py", "src/project_knowledge/evidence.py")),
    ("self-draft-lifecycle", ("src/project_knowledge/knowledge.py", "src/project_knowledge/service.py", "src/project_knowledge/retrieval.py", "src/project_knowledge/semantic.py")),
    ("self-feature-retrieval", ("src/project_knowledge/semantic.py", "src/project_knowledge/retrieval.py", "src/project_knowledge/cli.py")),
    ("self-proposal-stable-id", ("src/project_knowledge/proposal.py", "src/project_knowledge/models.py", "src/project_knowledge/schemas.py")),
    ("self-proposal-apply-conflict", ("src/project_knowledge/proposal.py", "src/project_knowledge/util.py")),
    ("self-draft-proposal-promotion", ("src/project_knowledge/semantic.py", "src/project_knowledge/proposal.py", "src/project_knowledge/cli.py")),
    ("self-proposal-delete-adr", ("src/project_knowledge/models.py", "src/project_knowledge/schemas.py", "src/project_knowledge/proposal.py")),
    ("self-semantic-update-queue", ("src/project_knowledge/service.py", "src/project_knowledge/proposal.py")),
    ("self-task-classification", ("src/project_knowledge/retrieval.py",)),
    ("self-retrieval-explanation", ("src/project_knowledge/retrieval.py",)),
    ("self-bounded-multihop", ("src/project_knowledge/retrieval.py",)),
    ("self-feature-development-context", ("src/project_knowledge/retrieval.py",)),
    ("self-context-unknowns", ("src/project_knowledge/retrieval.py",)),
)
```

Also assert:

```python
samples = load_dataset(Path("evaluation/questions.jsonl"))
self.assertEqual(len(samples), 50)
self.assertEqual(
    [(item["id"], tuple(item["expected_files"])) for item in samples[:40]],
    ORIGINAL_EXPECTED_FILES,
)
```

- [ ] **Step 2: Append the 10 exact hard-negative records**

Append these exact compact JSON objects as ten new JSONL lines:

```jsonl
{"schema_version":1,"id":"self-ranking-exact-over-test-noise","task":"tests/test_ranking.py 也包含 rank_files 字样时，生产 rank_files 的确定性文件重排实现在哪里？","category":"workflow","expected_files":["src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/ranking.py::rank_files"],"max_tokens":900}
{"schema_version":1,"id":"self-ranking-qualified-symbol","task":"RankingPolicy 如何定义 policy-v1 的核心阈值、补充阈值和文件上限，而不是命中其他文档里的 ranking policy 文本？","category":"configuration","expected_files":["src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/ranking.py::RankingPolicy"],"max_tokens":900}
{"schema_version":1,"id":"self-ranking-path-over-content-frequency","task":"src/project_knowledge/ranking.py 中哪个入口计算文件证据分数？忽略文档里高频出现的 ranking 说明。","category":"workflow","expected_files":["src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/ranking.py::score_candidate"],"max_tokens":800}
{"schema_version":1,"id":"self-ranking-one-hop-over-two-hop","task":"score_candidate 如何保证一跳代码图关系的分值高于二跳依赖，并只使用最短 graph_hop？","category":"impact","expected_files":["src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/ranking.py::score_candidate"],"max_tokens":900}
{"schema_version":1,"id":"self-ranking-direct-knowledge-source","task":"KnowledgeAPI.context 如何把已选知识记录的直接来源交给 rank_files，而不把同一知识页引用的全部文件都当作核心证据？","category":"workflow","expected_files":["src/project_knowledge/retrieval.py","src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/retrieval.py::KnowledgeAPI.context","src/project_knowledge/ranking.py::rank_files"],"max_tokens":1400}
{"schema_version":1,"id":"self-context-core-supporting-contract","task":"KnowledgeAPI.context 如何返回 core_files、supporting_files、files 有序并集和 file_rankings？","category":"workflow","expected_files":["src/project_knowledge/retrieval.py","src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/retrieval.py::KnowledgeAPI.context","src/project_knowledge/ranking.py::RankingResult"],"max_tokens":1400}
{"schema_version":1,"id":"self-ranking-fallback-gate","task":"fallback_rank_files 如何保持运行时可用，同时 evaluate_quality_gate 为什么必须拒绝 ranking fallback？","category":"quality_gate","expected_files":["src/project_knowledge/ranking.py","src/project_knowledge/evaluate.py"],"expected_symbols":["src/project_knowledge/ranking.py::fallback_rank_files","src/project_knowledge/evaluate.py::evaluate_quality_gate"],"max_tokens":1200}
{"schema_version":1,"id":"self-ranking-stale-shield","task":"KnowledgeAPI.context 在文件重排前如何继续屏蔽 pending 或 stale 来源，避免旧内容因 rank_files 再次出现？","category":"stale_detection","expected_files":["src/project_knowledge/retrieval.py","src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/retrieval.py::KnowledgeAPI.context","src/project_knowledge/ranking.py::rank_files"],"expected_stale":false,"max_tokens":1200}
{"schema_version":1,"id":"self-evaluation-production-ranker","task":"_retrieve 如何让 Markdown 和 grep 只生成候选，并统一调用生产 rank_files 而不维护第二套排序？","category":"workflow","expected_files":["src/project_knowledge/evaluate.py","src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/evaluate.py::_retrieve","src/project_knowledge/ranking.py::rank_files"],"max_tokens":1200}
{"schema_version":1,"id":"self-ranking-token-core-protection","task":"KnowledgeAPI._fit_context 如何在 Token 预算不足时先裁剪 supporting_files，并保护 core_files？","category":"performance","expected_files":["src/project_knowledge/retrieval.py","src/project_knowledge/ranking.py"],"expected_symbols":["src/project_knowledge/retrieval.py::KnowledgeAPI._fit_context","src/project_knowledge/ranking.py::RankingResult"],"max_tokens":800}
```

Do not add `acceptable_supporting_files` to these ten records unless a human reviewer approves an explicit additional file in a later, separately reviewed dataset change.

- [ ] **Step 3: Raise thresholds without deleting old gates**

Update `evaluation/thresholds.json`:

- `frozen_for_version` -> `0.1.29`;
- `minimum_samples` -> `50`;
- add hybrid minimums `core_file_recall: 0.85`, `core_file_precision: 0.40`, `file_precision: 0.22` while keeping `file_recall: 0.94` and all current semantic minimums;
- add hybrid maximums `average_returned_files: 10`, `average_context_tokens: 1000`, `ranking_fallback_rate: 0.0`;
- raise code `file_precision` to `0.25`, Markdown to `0.30`, grep to `0.32`, retaining their recall and cost gates;
- add `ranking_fallback_rate: 0.0` to every available strategy maximum;
- add allowed regression entries of `0.02` for core precision/recall and nDCG, `0.5` for average file counts, and `0.0` for fallback rate.

- [ ] **Step 4: Run dataset and absolute-gate tests**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest tests/test_evaluate.py -q
rtk .\.venv\Scripts\project-kb.exe evaluate evaluation\questions.jsonl --project . --strategy all --thresholds evaluation\thresholds.json --output evaluation\reports\wp12a-absolute.json --quiet
```

Expected: tests pass and the absolute evaluation exits 0. Do not supply a baseline in this step.

If an absolute metric fails, inspect the named samples and `score_breakdown`. Change exactly one `policy-v1` signal or threshold per red/green cycle, add a named ranking unit test proving the conflict, rerun the focused test, then rerun the complete absolute gate. Never change an existing strict answer or lower an approved quality threshold.

- [ ] **Step 5: Commit Task 6**

```powershell
rtk git add -- evaluation/questions.jsonl evaluation/thresholds.json tests/test_evaluate.py src/project_knowledge/ranking.py evaluation/reports/wp12a-absolute.json
rtk git commit -m "test: gate WP-12A retrieval precision"
```

---

### Task 7: Documentation, Curated Review, CI Reference, and Single Version Bump

**Files:**
- Modify: `docs/project-knowledge-system-audit.md`
- Modify: `docs/knowledge/curated/architecture.md`
- Modify: `docs/knowledge/curated/conventions.md`
- Modify: `docs/knowledge/curated/feature-guide-generation.md`
- Modify: `.github/workflows/quality.yml`
- Modify through script: `src/project_knowledge/__init__.py`
- Modify through script: `CHANGELOG.md`
- Modify through script: `plugins/project-knowledge/.codex-plugin/plugin.json`

**Interfaces:**
- Consumes: passing Tasks 1-6 and the absolute evaluation report.
- Produces: auditable requirement status, verified curated behavior, CI baseline target, version 0.1.29, and matching changelog/plugin metadata.

- [ ] **Step 1: Update curated knowledge from verified implementation only**

Document in the three curated pages:

- architecture: candidate generation and ranking are separate; `ranking.py` owns policy-v1 and core/supporting partitioning;
- conventions: strict core metrics use only `expected_files`; supporting labels are diagnostic; fallback rate must be zero in formal evaluation;
- feature guide generation: `KnowledgeAPI.context()` exposes ordered `core_files`, `supporting_files`, `files`, `file_rankings`, and structured ranking status while stale shielding still precedes ranking.

Add source markers for `src/project_knowledge/ranking.py`, `src/project_knowledge/retrieval.py`, `src/project_knowledge/evaluate.py`, and `evaluation/thresholds.json` next to the verified claims. Do not mark any model-generated conclusion verified.

- [ ] **Step 2: Update the audit only after the evidence exists**

Add a WP-12A evidence section listing the new tests, 50-sample report, exact metrics, CodeGraph validation, and remaining static-analysis limitations. Change RT-010 to completed only if every approved gate passes; otherwise leave it “未达标” and record the exact failing metrics.

- [ ] **Step 3: Point CI to the forthcoming 0.1.29 baseline**

Change only the quick evaluation baseline argument:

```yaml
--baseline evaluation/baselines/self-repo-0.1.29.json
```

Keep the finalization check and report upload steps intact. Run:

```powershell
rtk .\.venv\Scripts\python.exe scripts\validate_ci_workflow.py
```

Expected: exit 0.

- [ ] **Step 4: Perform the one allowed version bump**

```powershell
rtk .\.venv\Scripts\python.exe scripts\bump_version.py "提高检索精确率并增加核心证据重排"
rtk .\.venv\Scripts\python.exe -m project_knowledge --version
```

Expected: version output is `project-kb 0.1.29`; `CHANGELOG.md` has one new 0.1.29 entry and the plugin manifest version is 0.1.29.

- [ ] **Step 5: Run source-phase verification**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest -q
rtk .\.venv\Scripts\python.exe scripts\validate_ci_workflow.py
rtk .\.venv\Scripts\python.exe scripts\validate_codegraph_adapter.py
rtk .\.venv\Scripts\python.exe -m project_knowledge --version
rtk git diff --check
```

Expected: all tests and validators pass, version is 0.1.29, and diff check is clean.

- [ ] **Step 6: Commit source, tests, docs, CI, and version metadata**

```powershell
rtk git add -- src/project_knowledge/ranking.py src/project_knowledge/retrieval.py src/project_knowledge/evaluate.py src/project_knowledge/__init__.py tests/test_ranking.py tests/test_retrieval_wp06.py tests/test_evaluate.py evaluation/questions.jsonl evaluation/thresholds.json evaluation/reports/wp12a-absolute.json docs/project-knowledge-system-audit.md docs/knowledge/curated/architecture.md docs/knowledge/curated/conventions.md docs/knowledge/curated/feature-guide-generation.md .github/workflows/quality.yml CHANGELOG.md plugins/project-knowledge/.codex-plugin/plugin.json
rtk git commit -m "feat: improve retrieval precision"
```

Do not stage `evaluation/reports/latest.json`, the 0.1.29 frozen baseline, manifest, or generated knowledge in this source-phase commit; Task 8 creates them from the clean source commit.

---

### Task 8: Freeze the Clean Baseline and Finalize Generated Knowledge

**Files:**
- Generate: `evaluation/reports/latest.json`
- Generate: `evaluation/baselines/self-repo-0.1.29.json`
- Generate/update: `.project-kb/manifest.json`
- Generate/update: `docs/knowledge/generated/**`
- Possibly modify after review: curated knowledge named by `knowledge_impact`

**Interfaces:**
- Consumes: the clean source commit from Task 7.
- Produces: a comparable 0.1.29 report/baseline, synchronized knowledge evidence, a generated-output commit, and final `ready` status.

- [ ] **Step 1: Confirm the source tree is clean and run the formal evaluation without a baseline**

```powershell
rtk git status --short
rtk .\.venv\Scripts\project-kb.exe evaluate evaluation\questions.jsonl --project . --strategy all --thresholds evaluation\thresholds.json --output evaluation\reports\latest.json --quiet
```

Expected: status is empty before evaluation; evaluation exits 0; report says `working_tree="clean"`, `samples=50`, and `quality_gate.passed=true`.

- [ ] **Step 2: Freeze the exact clean report as the 0.1.29 baseline**

Use a structured JSON load/write or a direct generated-file copy so the baseline content is byte-for-byte the formal report except for no manual edits:

```powershell
rtk powershell.exe -NoProfile -Command "Copy-Item -LiteralPath 'evaluation/reports/latest.json' -Destination 'evaluation/baselines/self-repo-0.1.29.json'"
```

Then rerun the same evaluation with the frozen baseline:

```powershell
rtk .\.venv\Scripts\project-kb.exe evaluate evaluation\questions.jsonl --project . --strategy all --thresholds evaluation\thresholds.json --baseline evaluation\baselines\self-repo-0.1.29.json --output evaluation\reports\latest.json --quiet
```

Expected: exit 0, identical dataset/source hashes, no regression failures, and only explicitly allowed availability warnings.

- [ ] **Step 3: Synchronize generated knowledge**

```powershell
rtk .\.venv\Scripts\project-kb.exe finalize . --json
```

Expected: `generated_commit_required` with an explicit generated file list, or `ready` if no generated outputs changed. Review every listed generated diff and ensure no stale or conflicted curated record remains.

- [ ] **Step 4: Commit only reviewed generated outputs**

Stage the two formal evaluation artifacts, manifest, and the complete explicit generated-knowledge allowlist below. Unchanged paths are harmless; if finalize reports a generated path outside this allowlist, stop and amend/review the plan before staging it.

```powershell
rtk git add -- evaluation/reports/latest.json evaluation/baselines/self-repo-0.1.29.json .project-kb/manifest.json docs/knowledge/generated/entrypoints.md docs/knowledge/generated/project-map.md docs/knowledge/generated/routes.md docs/knowledge/generated/test-map.md docs/knowledge/generated/modules/evaluation.md docs/knowledge/generated/modules/github.md docs/knowledge/generated/modules/plugins.md docs/knowledge/generated/modules/project_knowledge.md docs/knowledge/generated/modules/root.md docs/knowledge/generated/modules/scripts.md docs/knowledge/generated/modules/tests.md
rtk git commit -m "docs: synchronize WP-12A release evidence"
```

Do not use `git add -A`.

- [ ] **Step 5: Run final verification from the generated-output commit**

```powershell
rtk .\.venv\Scripts\python.exe -m pytest -q
rtk .\.venv\Scripts\python.exe scripts\validate_ci_workflow.py
rtk .\.venv\Scripts\python.exe scripts\validate_codegraph_adapter.py
rtk .\.venv\Scripts\python.exe -m project_knowledge --version
rtk .\.venv\Scripts\project-kb.exe finalize . --check --json
rtk git status --short --branch
```

Expected:

- all tests and validators pass;
- version is 0.1.29 and CHANGELOG contains the matching entry;
- finalization returns `status="ready"` and `verification_aligned=true`;
- stale and conflicted knowledge counts are zero;
- working tree is clean;
- generated knowledge is synchronized and curated review is explicitly recorded.

- [ ] **Step 6: Review completion against the spec**

Check every item in the design’s completion definition. If any metric, test, CodeGraph validation, doc, version, baseline, knowledge review, or finalization condition is missing, leave RT-010 incomplete and report the exact remaining blocker instead of declaring WP-12A complete.
