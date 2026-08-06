---
name: project-knowledge
description: Retrieve source-traceable, freshness-aware project knowledge through the local Project Knowledge MCP server. Use for architecture explanations, feature implementation, cross-module bug fixes, public-interface changes, refactors, code review, impact analysis, or project knowledge maintenance in an initialized repository.
---

# Project Knowledge

Use the knowledge base as an index into live source, not as a substitute for verification.

## Workflow

1. Call `knowledge_status` before broad repository exploration.
2. If `pending_files` is non-empty, request `project-kb sync` or read those files live before relying on indexed facts.
3. Call `knowledge_context` with the user's exact task and an appropriate token budget.
4. Inspect each returned record's `confidence`, `freshness`, and `sources`.
5. Call `knowledge_impact` with the files or stable symbol IDs that may change.
6. Read only the cited source anchors needed to confirm behavior and static-analysis gaps.
7. Implement the change and run the returned verification commands plus any task-specific checks.
8. Run `project-kb sync --task-summary "<intent>"` after source changes when command execution is available.
9. Report stale curated records or semantic changes that need human review; never silently rewrite curated prose or accepted ADRs.

## Trust Rules

- Use `verified` and `fresh` curated knowledge as project intent.
- Use `generated` and `fresh` knowledge as deterministic or explicitly bounded code facts.
- Verify `inferred`, `potentially_stale`, `stale`, or `conflicted` claims in live source.
- Treat unresolved call edges and generic-parser output as possible anchors, not proven runtime paths.
- Do not return an indexed source excerpt when `knowledge_status` reports that file as pending.

## Tool Selection

- Use `knowledge_context` as the default task entry point.
- Use `knowledge_search` for modules, workflows, recipes, glossary terms, and ADRs.
- Use `knowledge_get` when a stable knowledge ID is already known.
- Use `knowledge_impact` before modifying shared symbols or cross-module behavior.
- Use `knowledge_status` to check commits, pending files, parse errors, stale knowledge, and watcher health.

