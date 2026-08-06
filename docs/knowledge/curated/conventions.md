# 约定

- 保持与 Python 3.11+ 兼容，不强制依赖网络服务或第三方运行时软件包。
- 保持确定性生成事实与经过评审的人工意图相互分离。
- 自动生成知识、知识索引和初始人工模板默认使用中文；代码标识、路径、记录 ID 和机器接口枚举不得为了显示翻译而改变。
- 项目以 `0.1.0` 为版本基线。后续每批修改或新增内容都通过 `python scripts/bump_version.py "中文变更说明"` 将补丁版本递增一次，并同步记录到 `CHANGELOG.md`；同一批变更中的知识同步不重复递增。
- 返回给 AI 客户端的知识必须包含相对来源路径、稳定符号 ID、可信度和新鲜度。
- 状态变更使用原子文件替换和单写入者锁；索引变更使用 SQLite 事务。
- 如果 `status` 已知某个来源正在等待同步，不得返回从该来源派生的旧内容。
- 使用 `unittest` 为生命周期、新鲜度、隐私、MCP 和标记所有权变更添加回归覆盖。
- 任务上下文不得超过请求的总 Token 预算，排序后的知识页面最多返回四个。
- 保留由标记边界保护的集成块周围的用户自有内容。
- 完整重建期间保留人工知识和 ADR 的来源哈希基线，不得把索引重建视为人工验证。

<!-- project-kb:source file="pyproject.toml" -->
<!-- project-kb:source file="src/project_knowledge/__init__.py" -->
<!-- project-kb:source file="src/project_knowledge/versioning.py" -->
<!-- project-kb:source file="scripts/bump_version.py" -->
<!-- project-kb:source file="tests/test_integration.py" -->
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="src/project_knowledge/util.py" -->
