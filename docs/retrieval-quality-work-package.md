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

## Acceptance boundary

The final plan requires at least 300 questions, at least 30 samples per query type, at least 3 stable repositories or snapshots, locked source and CodeGraph hashes, and clean-environment reproducibility. This Phase 0 batch intentionally does not claim that gate is complete.
