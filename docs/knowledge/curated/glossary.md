# 术语表

- **自动生成知识（Generated Knowledge）**：PKS 可根据当前代码索引以原子方式替换的确定性 Markdown。
- **人工维护知识（Curated Knowledge）**：经过人工评审的项目意图和规则。PKS 可以更新其状态和生成块，但不会修改人工正文。
- **来源锚点（Source anchor）**：用于追溯知识陈述的相对文件路径或稳定符号 ID。
- **新鲜度（Freshness）**：来源哈希状态，包括 `fresh`、`potentially_stale`、`stale` 或 `conflicted`。
- **可信度（Confidence）**：知识来源类型，包括 `verified`、`generated` 或 `inferred`。
- **待同步来源（Pending source）**：当前哈希与索引哈希不同的工作区文件。依赖该来源的旧内容会被屏蔽。
- **变更集（ChangeSet）**：对一批已变更文件、符号、模块、知识和验证证据的描述。
- **更新提案（Proposal）**：需要评审的语义知识更新，不会静默应用到人工维护正文。

<!-- project-kb:source file="src/project_knowledge/models.py" -->
<!-- project-kb:source file="docs/project-knowledge-system-design.md" -->
