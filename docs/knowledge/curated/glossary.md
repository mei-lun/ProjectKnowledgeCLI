# 术语表

- **自动生成知识（Generated Knowledge）**：PKS 可根据当前代码索引以原子方式替换的确定性 Markdown。
- **人工维护知识（Curated Knowledge）**：经过人工评审的项目意图和规则。PKS 可以更新其状态和生成块，但不会修改人工正文。
- **来源锚点（Source anchor）**：用于追溯知识陈述的相对文件路径或稳定符号 ID。
- **新鲜度（Freshness）**：来源哈希状态，包括 `fresh`、`potentially_stale`、`stale` 或 `conflicted`。
- **可信度（Confidence）**：知识来源类型，包括 `verified`、`generated` 或 `inferred`。
- **待同步来源（Pending source）**：当前哈希与索引哈希不同的工作区文件。依赖该来源的旧内容会被屏蔽。
- **内容新鲜度（Content freshness）**：索引文件哈希与当前工作区内容是否一致，独立于 Git HEAD 是否变化。
- **提交对齐（Commit alignment）**：索引元数据是否已在当前 Git HEAD 上完成校验；源码内容未变化时也可以通过元数据同步完成对齐。
- **未审阅模板（Unreviewed template）**：带有 `project-kb:template` 标记的初始人工文档，只能以 `inferred` 可信度参与检索。
- **变更集（ChangeSet）**：对一批已变更文件、符号、模块、知识和验证证据的描述。
- **语义更新队列（Semantic Update Queue）**：同步 ChangeSet 后形成的本地待处理记录，用于追踪哪些源码变化仍需语义生成或已经关联 Proposal。
- **更新提案（Proposal）**：具有稳定 ID、目标/来源哈希、结构化 Patch operation 和完整审核记录的语义知识更新；pending 状态不会修改 curated。
- **Proposal 不变量**：未审核 Proposal 不修改 curated。
- **生成区块（generated block）**：由 `project-kb:generated` 起止标记界定、允许 Proposal 精确替换或删除的局部内容；标记外人工正文不属于自动化写入范围。
- **过期提案（conflicted Proposal）**：提案生成后目标或来源发生变化而被冻结的审计记录，必须基于当前内容重新生成，不能直接应用。
- **证据包（EvidencePack）**：按相对路径、文件数和 Token 上限组装，排除高风险路径、完成 Secret 脱敏并具有稳定哈希的模型输入。
- **模型提供者（ModelProvider）**：隔离模型能力、网络授权和结构化生成的可替换边界；默认实现为 disabled。
- **脱敏（Redaction）**：在预览、外发或持久化前，用不含原值的类型占位符替换 Secret；检测结果只记录类型和行号。
- **Provider 检查点（Provider checkpoint）**：只记录请求哈希、版本和执行状态的恢复元数据，不保存证据正文、模型凭据或 Secret。
- **语义草案（Semantic Draft）**：模型根据有限 EvidencePack 生成、通过结构和引用校验但尚未人工验证的知识；ownership 为 `draft`，可信度为 `generated`。
- **功能指南（Feature Guide）**：围绕一个功能组织职责、入口、依赖、状态、不变量、扩展点、测试、陷阱和未决问题的语义知识分片。
- **工作流（Workflow）**：带有从 1 连续递增步骤和逐步来源的当前功能执行过程。
- **开发配方（Recipe）**：包含目标、前置条件、实施步骤、验证和回滚的功能开发指导。
- **未决问题（unknowns）**：证据不足、不能作为确定性陈述落入草案正文的判断，以及确认它所需的补充证据。

<!-- project-kb:source file="src/project_knowledge/models.py" -->
<!-- project-kb:source file="src/project_knowledge/evidence.py" -->
<!-- project-kb:source file="src/project_knowledge/provider.py" -->
<!-- project-kb:source file="src/project_knowledge/semantic.py" -->
<!-- project-kb:source file="src/project_knowledge/proposal.py" -->
<!-- project-kb:source file="src/project_knowledge/schemas.py" -->
<!-- project-kb:source file="docs/project-knowledge-system-design.md" -->

<!-- project-kb:generated id="wp08-terms" -->
- **配置模式版本（Config schema version）**：用于区分可迁移配置语义的整数版本；0.1.10 当前版本为 1。
- **配置迁移（Config migration）**：通过 dry-run 预览并显式应用的前向升级；必须保留未知用户字段，对未来高版本显式失败。
- **客户端所有权标记（Client ownership marker）**：Project Knowledge 在 Claude、Cursor、Gemini 或 AGENTS 文件中唯一允许自动更新的受控区块；标记外内容属于用户。
<!-- /project-kb:generated -->
