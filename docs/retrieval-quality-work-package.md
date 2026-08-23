# Retrieval Quality Optimization Work Package

## WP-RQ-01 — Phase 0 baseline and observability

This work package follows `ProjectKnowledgeCLI检索质量优化技术方案与验收方案.md`. It establishes a reproducible baseline and explainable retrieval protocol without changing default retrieval behavior.

| Requirement | Deliverable | Evidence | Status |
| --- | --- | --- | --- |
| RQ-P0-001 | Add `CanonicalFile`, `CanonicalSymbol`, and `RetrievalCandidate` with repository, snapshot, hash, and freshness identity | `src/project_knowledge/models.py`, `tests/test_retrieval_phase0.py` | complete |
| RQ-P0-002 | Add opt-in `debug=true` stage traces to `knowledge_context/search/impact`; preserve default response compatibility | `src/project_knowledge/retrieval.py`, `src/project_knowledge/mcp.py` | complete |
| RQ-P0-003 | Freeze the gardenserver CodeGraph 1.5.0 snapshot and 12 verified Phase 0 seed questions | `evaluation/questions-gardenserver-phase0.jsonl`, `evaluation/snapshots/gardenserver-phase0.json` | complete |
| RQ-P0-004 | Produce a reproducible real-project baseline with recall, precision, latency, fallback, and trace probe data | `evaluation/reports/gardenserver-phase0-0.1.35.json` | complete |
| RQ-P0-005 | Expand to at least 300 questions across at least 3 stable repositories or snapshots | Stratified JSONL and locked source/CodeGraph hashes | pending |

The Phase 0 seed is not a production quality gate. The report records insufficient sample size, below-threshold recall/precision, and high CodeGraph latency; thresholds must not be lowered and builtin fallback must not be restored to claim success.

## WP-RQ-02 — Phase 1 multi-channel candidate recall

Phase 1 keeps the default public response stable and changes candidate generation only. Each candidate now retains one or more typed recall channels, and every channel has an independent cap before ranking.

| Requirement | Deliverable | Evidence | Status |
| --- | --- | --- | --- |
| RQ-P1-001 | Preserve original query terms while adding deterministic, reviewable aliases | `src/project_knowledge/retrieval.py`, `tests/test_retrieval_phase1.py` | complete |
| RQ-P1-002 | Add independent path, symbol, lexical, knowledge, graph, and test/config recall channels | `src/project_knowledge/retrieval.py`, `src/project_knowledge/ranking.py` | complete |
| RQ-P1-003 | Distinguish direct graph expansion from multi-hop expansion and retain channel provenance in debug traces | `src/project_knowledge/retrieval.py`, `tests/test_retrieval_phase1.py` | complete |
| RQ-P1-004 | Enforce per-channel candidate limits and exclude pending files | `src/project_knowledge/retrieval.py`, `tests/test_retrieval_phase1.py` | complete |
| RQ-P1-005 | Add 20 verified gardenserver challenge samples covering aliases, exact paths, knowledge, impact, tests/config, and negative noise | `evaluation/questions-gardenserver-phase1.jsonl` | complete |
| RQ-P1-006 | Meet the final Phase 1 quality gate on at least 300 questions and at least 3 stable repositories/snapshots | Locked multi-project evaluation report | pending |

On the locked 12-question Phase 0 seed, file recall improved from `0.583333` to `0.833333`, core-file recall from `0.583333` to `0.791667`, symbol recall from `0.700000` to `0.800000`, and nDCG@5 from `0.535890` to `0.687006`. On the new 20-question challenge set, file recall is `0.841667`, core-file recall is `0.816667`, symbol recall is `0.708333`, and nDCG@5 is `0.654721`.

The increased average returned-file count (`4.333333` to `8.083333` on the Phase 0 seed) is an intentional recall-first trade-off. Business-invariant and same-domain component selection failures remain and are assigned to Phase 2 ranking; Phase 1 does not claim the production gate.

## WP-RQ-03 — Phase 2 symbol-first and query-profile ranking

Phase 2 replaces the default file policy with explainable `policy-v2` while retaining `policy-v1` as a project configuration rollback. It remains fully local and deterministic; no external reranker is enabled.

| Requirement | Deliverable | Evidence | Status |
| --- | --- | --- | --- |
| RQ-P2-001 | Infer call-path, impact, extension-point, invariant, design-reason, configuration, test/config, and workflow profiles separately from development intent | `src/project_knowledge/retrieval.py`, `tests/test_retrieval_phase2.py` | complete |
| RQ-P2-002 | Rank symbols before file assembly, prioritizing explicit qualified symbols and profile-specific aliases | `src/project_knowledge/retrieval.py`, `tests/test_retrieval_phase2.py` | complete |
| RQ-P2-003 | Add explainable definition, channel-consensus, profile-role, generic-symbol, vendor, generated, high-degree, auxiliary-source, and test-noise features | `src/project_knowledge/ranking.py`, `tests/test_ranking.py` | complete |
| RQ-P2-004 | Preserve both implementation and relevant test roles in Core for explicit test queries | `src/project_knowledge/ranking.py`, `tests/test_ranking.py` | complete |
| RQ-P2-005 | Keep `policy-v1` as a validated configuration rollback and expose score breakdowns in debug traces | `src/project_knowledge/config.py`, `src/project_knowledge/retrieval.py`, `tests/test_config.py` | complete |
| RQ-P2-006 | Add an optional Top-N model reranker with a validated offline provider and latency evidence | Provider-backed evaluation report | pending |
| RQ-P2-007 | Pass the final quality gate on at least 300 questions and at least 3 stable repositories/snapshots | Locked multi-project evaluation report | pending |

On the locked 12-question seed, `policy-v2` reaches `1.000000` file recall, core-file recall, symbol recall, and success rate; nDCG@5 is `0.938488`. On the 20-question challenge set, file recall and symbol recall are `1.000000`, core-file recall is `0.966667`, core-file precision is `0.300000`, nDCG@5 is `0.738156`, and success rate is `1.000000`. Both sets report zero ranking fallback.

The result validates deterministic symbol-first ranking on the frozen gardenserver snapshot, not the production gate. Core precision remains below the final `0.50` target, P95 end-to-end latency remains above 12 seconds, and the dataset still has only 32 questions from one stable snapshot. Optional model reranking is deliberately left pending until an offline provider and independent samples can demonstrate a real gain without weakening local-first behavior.

## Release evidence alignment

Release evidence is maintained separately from Phase 3 behavior so that regenerating an active report is not misreported as context-production functionality.

| Requirement | Deliverable | Evidence | Status |
| --- | --- | --- | --- |
| RQ-REL-001 | Keep the active evaluation report, README quality summary, CodeGraph status document, audit header, package version, and CI provenance validation aligned | `evaluation/reports/latest.json`, `README.md`, `docs/codegraph-evaluation-current.md`, `scripts/validate_evaluation_provenance.py`, `tests/test_evaluation_provenance.py`, `tests/test_documentation_roadmap.py` | complete |

## WP-RQ-04 — Phase 3 context production and release gates

Phase 3 follows the original technical plan. Existing partial behavior is recorded as partial and remains incomplete until positive and negative tests, real-project evaluation, documentation, versioning, and knowledge synchronization all pass.

| Requirement | Deliverable | Evidence | Status |
| --- | --- | --- | --- |
| RQ-P3-001 | Return explicit `core`, `supporting`, and `optional` tiers with stable ordering and explainable tier transitions | `RankingPolicy.optional_limit`, `RankingResult.optional_files`, tiered `file_rankings`, fallback ordering, and positive/negative tests in `tests/test_ranking.py` and `tests/test_retrieval_wp06.py` | complete |
| RQ-P3-002 | Rework token-budget trimming so optional and supporting evidence are removed before required Core symbols and required relation paths | `src/project_knowledge/context_evidence.py` provides a budget-independent required planner; `src/project_knowledge/retrieval.py` preserves required symbol signature/span and complete ordered relation paths, reports `pre_required_evidence`, `post_required_evidence`, `context_incomplete`, `missing_required_evidence`, and strict insufficient-budget status; positive/negative tests in `tests/test_context_evidence.py` and `tests/test_retrieval_wp06.py` | complete |
| RQ-P3-003 | Expose low-confidence, `context_incomplete`, and `needs_source_check` states without presenting uncertain results as verified facts | `context_status` state/confidence/reasons contract in `KnowledgeAPI.context`, evaluator forwarding, and positive/negative status tests in `tests/test_retrieval_wp06.py` | complete |
| RQ-P3-004 | Persist complete debug traces and publish separate lexical, CodeGraph, ranking, and context-assembly P50/P95/P99 measurements | Trace schema v2 records search/impact/context stage durations and status, CodeGraph evidence, pre/post required evidence, per-step trim events with token deltas, evaluator stage percentiles, and performance target pass/fail results; multi-snapshot production execution remains a P3-005 gate | complete |
| RQ-P3-005 | Block regressions in CI using the locked multi-snapshot dataset, stale/branch/worktree checks, token-budget tests, and performance smoke tests | Strict-live provenance validation, fail-closed performance smoke gate, and dataset cardinality/snapshot validator are implemented; final locked 300-question multi-snapshot manifest and CI evaluation matrix still required | partial |

## Acceptance boundary

The final plan requires at least 300 questions, at least 30 samples per query type, at least 3 stable repositories or snapshots, locked source and CodeGraph hashes, and clean-environment reproducibility. The current Phase 0–2 baseline intentionally does not claim that gate is complete.

P3-005 remains blocked on approved evaluation data rather than implementation placeholders: the repository currently has 95 questions across six JSONL seeds, but only 77 carry `answer_status=verified`; all gardenserver Phase 0/1/2 reports reuse one stable snapshot, and no question carries a repository/snapshot identity. No synthetic questions or duplicated snapshots are counted toward the production gate.

The production-data validator is `scripts/validate_evaluation_dataset.py`; it rejects fewer than 300 questions, fewer than 30 samples per query type, or fewer than 3 identified snapshots. The scheduled CI performance job now validates the CodeGraph adapter and uses `--enforce-gate`; active report provenance can be checked against live HEAD, index, dataset, and CodeGraph snapshot with `--strict-live`.
