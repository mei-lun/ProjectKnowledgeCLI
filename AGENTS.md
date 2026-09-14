<!-- project-kb:instructions:start -->
Use the local Project Knowledge System before broad repository exploration.

1. Call `knowledge_status` at task start. If unavailable, run `project-kb doctor --json` and report the exact failure; do not claim knowledge was consulted.
2. If guidance is not initialized, call `knowledge_initialization_start`, then `knowledge_initialization_next` and `knowledge_initialization_submit` for every batch. Read the actual source files before submitting analyzedFiles. Save the category catalog, methodology, and guidance through `knowledge_draft_save`, following guidance_workflow.next_action until all required drafts exist. A code index alone is not initialized guidance.
3. Call `knowledge_context` for the user task and `knowledge_impact` before cross-module changes. Follow returned next_action/next_tool/arguments. Verify stale or inferred claims in live source; never treat unreviewed drafts as verified guidance.
4. Implement the change and run the returned verification commands and task-specific tests. Use a stable taskId for this development task, and reuse it on retries or lifecycle events.
5. After development and verification finish, automatically call `knowledge_task_complete` with developmentCompleted=true, summary, changedFiles, changedSymbols, and tests. Do not wait for the user to say the task is complete. Do not set userConfirmed=true without explicit user approval. The tool synchronizes code before planning; run `project-kb sync --task-summary "<intent>"` after other source changes.
6. Continue the returned next_tool with its arguments and evidence. Generate complete source-grounded content and submit each requested item through `knowledge_update_submit`; include taskId. Continue until generation_status=generated, no_update, skipped, or a concrete failure/manual-review requirement. A pending task is not a completed knowledge update. Explicit retry must revalidate the snapshot.
7. Stop automatic generation when all required drafts are ready. Call `knowledge_draft_confirm` only after explicit user approval of the specific draft and its contentHash. Draft generation and formal publication are separate; do not auto-confirm drafts or rewrite curated/decision knowledge.
8. Report the task ID, generated draft paths, synchronization result, failures or skipped reasons, and which knowledge still needs review. Hooks only synchronize/register events; do not assume a post-task hook runs in this client.
<!-- project-kb:instructions:end -->

## 版本管理

- 当前版本基线为 `0.1.0`，唯一版本源是 `src/project_knowledge/__init__.py`。
- 从基线建立后的下一批变更开始，任何修改或新增内容都必须在交付前运行 `python scripts/bump_version.py "中文变更说明"`，将补丁版本递增一次。
- 同一批变更只递增一次；由该批变更触发的知识文档同步不重复递增。
- 不要手动在其他文件复制版本号；构建元数据必须动态读取唯一版本源。
- 验证时运行 `python -m project_knowledge --version`，并确认 `CHANGELOG.md` 包含对应版本记录。

## 后续实施基线

- 后续功能开发必须以 `docs/project-knowledge-system-audit.md` 为需求和验收基线。
- 每批开发开始时标明对应的工作包（WP）和需求 ID，先补测试或评测样本，再实现行为。
- 只有实现、正负测试、相关评测、文档、版本和知识同步全部完成后，才能在审计报告中把需求标记为已完成。
- 不得用字段、配置占位符、空目录或未经真实样本验证的接口宣称功能已经完成。
