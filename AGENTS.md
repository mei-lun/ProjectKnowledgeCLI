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
