# 架构

Project Knowledge CLI 是一个本地优先的 Python 应用，CLI 和 MCP 适配器共用同一套核心能力。运行时不依赖第三方软件包。

## 职责边界

- `ProjectService` 负责初始化、同步、原子重建、文件监视、健康检查以及由标记边界保护的客户端集成。
- `CodeIndexEngine` 是可替换的结构索引边界。内置实现使用 Python AST 提取和保守的多语言模式匹配；CodeGraph Adapter 通过 1.5 公共 CLI 提供实时文件、符号、追踪、影响和受影响测试事实，不读取私有数据库且不回退 builtin。
- `KnowledgeStore` 负责私有 SQLite 架构、WAL 行为、一致性事务、全文检索和查询统计，其数据表不属于公共 API。
- `KnowledgeGenerator` 负责生成 Markdown、清单记录、发现人工文档中的来源标记以及维护新鲜度状态，绝不覆盖现有人工维护文档。初始 curated 模板带有未审阅标记，在删除标记前只能作为 `inferred` 信息，不能冒充已验证的项目意图。面向人的生成标题、说明、表头和索引状态默认使用中文，底层记录 ID 与接口枚举保持稳定。
- 原子重建会将人工知识和决策记录的哈希基线带入替换数据库。仅来源发生变化时，过期状态会跨重建保留；编辑人工维护正文是接受当前哈希的显式验证事件。
- 状态检查将完整文件哈希、Git 提交对齐和 SQLite 健康状态作为独立维度读取。`content_fresh` 表示索引内容与工作区一致，`commit_aligned` 表示这些内容已经在当前 HEAD 上完成校验；`verification_aligned` 只额外接受 PKS 自有生成物的提交边界。`FinalizationService` 把源码提交、同步、生成物提交和只读检查组织为确定性状态机，但不执行 Git 写操作。
- 发布到清单的 KnowledgeRecord 和写入事件目录的 ChangeSet 必须在落盘前通过运行时 Schema 验证。
- `KnowledgeAPI` 负责排序、Token 预算、待同步来源屏蔽、任务上下文和影响范围组装。它从当前选择的引擎获取实时结构事实，SQLite 只承担知识存储和兼容缓存；证据按直接命中、知识引用和依赖关系分阶段限额选择，并返回选择理由。
- `evaluate` 负责数据集校验、hybrid/grep + Read/code/Markdown/codegraph 策略隔离、锚点与语义指标、成本统计、质量阈值和历史基线退化判断。不可用策略必须明确报告，不得用其他实现伪造。
- `performance` 使用固定 500/5000 文件临时夹具测量分位数并执行过期屏蔽探针；`real_project` 只复制可索引文件到临时目录，以源目录全树快照证明真实项目未被写入。
- `EvidencePackBuilder` 只接受项目内相对路径，按文件数和 Token 上限组装脱敏证据并生成稳定哈希；高风险路径整文件排除。
- `ModelProvider` 隔离 disabled、Fake 和显式授权的 HTTP JSON Provider。`ModelRuntime` 在调用前后执行策略和 Schema 校验，并以不含证据正文的缓存键、检查点记录 Provider/模型/提示词/Schema 版本。
- `SemanticKnowledgeService` 在 Provider 与知识层之间负责 Feature Guide 草案。模型输出先通过 FeatureGuide/Workflow/Recipe Schema，再验证 EvidencePack 成员、路径、行号、文件与符号哈希；两层校验都早于缓存和知识写入。草案按功能分片并以 `draft/generated` 进入 Manifest、FTS 和 MCP，不能自行成为 `verified`。
- `ProposalService` 是语义草案进入 curated 的唯一写入边界。它以规范化意图生成稳定 ID，锁定目标与来源哈希，只允许结构化 generated block operation 或新增 ADR 草案；apply/reject 记录审核人、时间和理由，过期目标或来源会冻结为 `conflicted`。已有 ADR 不能被 Proposal 改写。同步产生的 ChangeSet 同时进入 Semantic Update Queue，关联 Proposal 后保留队列审计状态。
- 符号检索把完全匹配或稳定 ID 后缀匹配排在模糊命中之前；出现精确命中时不再混入同一检索词的低等级结果。only-Markdown 评测最多读取三页，并在总预算中提取任务相关片段。
- 除非任务明确要求项目概览，否则任务上下文不包含宽泛的项目地图；在图锚点之前最多返回四个已排序的知识页面。
- `MCPServer` 和参数解析器是这些服务之上的轻量适配器。
- `plugins/project-knowledge` 仅包含 Codex 集成；核心功能不依赖 Codex 也可以使用。

## 数据流

来源发现和解析结果写入事务型 SQLite 索引。确定性记录渲染到 `docs/knowledge/generated`，来源已校验的模型草案按功能写入 `docs/knowledge/drafts/features`。草案通过 Proposal 复核目标和全部本地来源哈希后，只能写入 curated 的明确 generated block；设计决策只能新增中文 ADR 草案，不能改写既有 ADR。MCP 查询将 generated、draft、curated、decision 与图锚点、实时新鲜度检查组合后返回。

同一路径内的首个符号保留普通稳定 ID；重复定义按 `@line` 后缀消歧，避免真实 Lua/Python 项目因 SQLite 符号唯一约束中止初始化。

<!-- project-kb:source file="src/project_knowledge/service.py" -->
<!-- project-kb:source file="src/project_knowledge/finalization.py" -->
<!-- project-kb:source file="src/project_knowledge/codegraph.py" -->
<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/store.py" -->
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="src/project_knowledge/retrieval.py" -->
<!-- project-kb:source file="src/project_knowledge/mcp.py" -->
<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
<!-- project-kb:source file="src/project_knowledge/performance.py" -->
<!-- project-kb:source file="src/project_knowledge/real_project.py" -->
<!-- project-kb:source file="src/project_knowledge/evidence.py" -->
<!-- project-kb:source file="src/project_knowledge/provider.py" -->
<!-- project-kb:source file="src/project_knowledge/semantic.py" -->
<!-- project-kb:source file="src/project_knowledge/proposal.py" -->
<!-- project-kb:source file="src/project_knowledge/schemas.py" -->

<!-- project-kb:generated id="proposal-capabilities" -->
- 0.1.15 WP-02：BuiltinCodeIndexEngine 对 Lua/Skynet 暴露可追溯入口证据，区分 Skynet 启动、协议派发和文件名推断入口；推断入口必须标注“需要人工确认”。
- 0.1.15 WP-02：真实项目只读 harness 增加范围 dry-run、排除项、风险列表和 revision 证据；无 SVN 命令时使用选中文件内容哈希作为稳定 file_hash_only revision，不向源目录写入。
- 0.1.15 WP-02：Lua/Skynet 入口知识页进入 generated Knowledge，静态启动与协议派发来源可被功能开发上下文检索；动态启动命令、运行时协议名和服务发现仍属于 unknowns。
<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/real_project.py" -->
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="evaluation/real_project_harness.py" -->
<!-- project-kb:source file="tests/test_wp02_evidence.py" -->
- 0.1.15 WP-09：only-Markdown 仍限制三页；仅当非测试源码模块候选得分至少达到当前第三页的 0.8 时才替换低优先页面，防止新增 generated 页面挤掉代码来源且避免为 recall 牺牲 precision。不得增加页数或降低阈值。
<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
<!-- project-kb:source file="tests/test_evaluate.py" -->
<!-- project-kb:source file="tests/test_wp02_knowledge.py" -->
<!-- /project-kb:generated -->
