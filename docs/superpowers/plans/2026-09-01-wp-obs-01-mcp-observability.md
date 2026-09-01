# WP-OBS-01 MCP Observability Implementation Plan

**Goal:** Record every Project Knowledge MCP message, response, relevant internal dependency span, and deterministic quality-analysis export without truncating non-secret payloads.

**Architecture:** A standalone observability module owns invocation context, recursive redaction, append-only audit events, span lifecycle, integrity validation, and deterministic export. MCP, CodeGraph, and Provider integrations call its narrow context-aware API without changing retrieval behavior.

**Tech Stack:** Python 3.11+ standard library, JSONL, `contextvars`, existing Project Knowledge schemas/evaluator, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-project-kb-mcp-observability-design.md`

## Global Constraints

- Work package: `WP-OBS-01`; requirements: `OBS-001` through `OBS-008`.
- Target release: `0.1.60`; `src/project_knowledge/__init__.py` remains the only version source.
- Add positive and negative tests before production behavior.
- Keep MCP stdout protocol-clean; audit failures are fail-open for MCP and fail-visible for export.
- Preserve complete non-secret inputs and outputs; recursive Secret redaction is mandatory and cannot be disabled.
- Do not use evaluation labels in runtime logging, retrieval, ranking, or export prediction extraction.
- Do not modify the user's existing `.project-kb/manifest.json` or generated project-map changes except through the final documented knowledge sync.
- Run repository commands through `rtk`.
- Bump the patch version exactly once after behavior, tests, evaluation samples, and documentation are complete.

## Task 1: Audit Core Contract Tests (`OBS-002`, `OBS-004`, `OBS-005`, `OBS-007`)

**Files:**

- Create `tests/test_mcp_observability.py`
- Create `src/project_knowledge/observability.py`
- Modify `src/project_knowledge/schemas.py`

Add failing tests for event IDs and sequencing, invocation/span closure, recursive redaction, append recovery gaps, corrupt lines, duplicate events, orphan spans, incomplete sessions, and deterministic export. Then implement only the standalone core required to pass them.

## Task 2: MCP Protocol Capture (`OBS-001`, `OBS-002`, `OBS-004`)

**Files:**

- Modify `tests/test_mcp_observability.py`
- Modify `tests/test_guidance_mcp.py`
- Modify `src/project_knowledge/mcp.py`

Add protocol tests for initialize, discover, ping, tools/list, every tools/call dispatch class, notifications, duplicate request IDs, unknown methods, invalid arguments, invalid JSON, and internal errors. Capture the exact emitted response before stdout, keep stdout clean, and close every parsed invocation with success or failure.

## Task 3: Dependency and Retrieval Spans (`OBS-003`, `OBS-005`)

**Files:**

- Modify `tests/test_mcp_observability.py`
- Modify `tests/test_codegraph.py`
- Modify `tests/test_provider.py`
- Modify `src/project_knowledge/codegraph.py`
- Modify `src/project_knowledge/provider.py`

Add tests first for real CodeGraph execution, cache hits, non-zero exits, timeouts, invalid JSON, Provider success/failure/retry/cache, parent linkage, full output capture, and Secret redaction. Instrument the existing central dependency boundaries without changing their returned values or exception contracts.

## Task 4: Validation and Analysis Export CLI (`OBS-006`, `OBS-007`, `OBS-008`)

**Files:**

- Modify `tests/test_mcp_observability.py`
- Modify `src/project_knowledge/observability.py`
- Modify `src/project_knowledge/cli.py`

Add `project-kb mcp-log validate` and `project-kb mcp-log export`. Validate event schema, session sequence, invocation closure, span parentage, duplicate IDs, gaps, corrupt JSON, and deterministic output. Extract returned files, symbols, knowledge IDs, call paths, extension points, invariants, design reasons, ranking diagnostics, and ground-truth reference from actual MCP responses.

## Task 5: Real MCP Evaluation Sample (`OBS-008`)

**Files:**

- Create a focused JSONL fixture under `evaluation/`
- Create or modify a validation script under `scripts/`
- Modify `tests/test_evaluate.py` or `tests/test_mcp_observability.py`

Run an actual stdio MCP session against a controlled initialized project, export its audit events, join an independent ground-truth fixture, and compute deterministic file, symbol, and call-path precision/recall. Prove deleting an event blocks official export.

## Task 6: Documentation and Audit State

**Files:**

- Modify `README.md`
- Modify `docs/evaluation-guide.md`
- Modify `docs/project-knowledge-system-audit.md`

Document raw paths, event/analysis schemas, privacy behavior, validation, export, ground-truth separation, metric interpretation, and ordered-only causality. Mark `OBS-001` through `OBS-008` complete only after all real tests and evaluation evidence pass.

## Task 7: Release and Knowledge Synchronization

**Commands:**

1. Run focused observability, MCP, CodeGraph, Provider, CLI, and evaluator tests.
2. Run the repository's full pytest suite and documented validation commands affected by the change.
3. Run `python scripts/bump_version.py "新增 MCP 全链路审计日志与质量分析导出"` exactly once.
4. Run `python -m project_knowledge --version` and verify the matching `CHANGELOG.md` entry.
5. Run `python -m project_knowledge sync --task-summary "实现 WP-OBS-01 MCP 全链路审计日志与质量分析导出"`.
6. Re-run status/check and report generated knowledge synchronization plus curated knowledge review needs.

