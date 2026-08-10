<!-- project-kb:instructions:start -->
Use the local Project Knowledge System before broad repository exploration.

1. Call `knowledge_status` at task start.
2. Call `knowledge_context` for the user task and `knowledge_impact` before cross-module changes.
3. Treat `verified` and `generated` facts according to their reported freshness; verify stale or inferred claims in live source.
4. Read only the source files needed to confirm and implement the change.
5. Run the returned verification commands or the repository's documented checks.
6. Report whether generated knowledge was synchronized and whether curated knowledge needs review.
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
