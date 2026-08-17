# 功能开发指南生成约定

0.1.30 人工复核：Feature Guide 的符号引用在生成与校验时使用 CodeGraph 实时查询和公开符号名，不再依赖 SQLite 符号缓存。

功能开发指导采用“确定性代码事实 + 受限模型语义组织 + 本地引用校验”的组合流程。静态分析负责候选范围、文件、符号和关系；模型负责把有限 EvidencePack 组织成 Feature Guide、Workflow 和 Recipe；系统在任何草案落库前验证 Schema、路径、符号、行号和哈希。

<!-- project-kb:source file="src/project_knowledge/semantic.py" -->
<!-- project-kb:source file="src/project_knowledge/schemas.py" -->

模型输出固定为 `draft`，不能声明为 `verified`。每条确定性陈述必须包含至少一个来源；无法从证据确认的结论必须进入 `unknowns`。已有 Markdown 或文本只能作为候选证据，不能默认成为源码权威。

<!-- project-kb:source file="src/project_knowledge/semantic.py" -->
<!-- project-kb:source file="src/project_knowledge/schemas.py" -->

Feature Guide 按功能独立写入 `docs/knowledge/drafts/features/`。`KnowledgeGenerator._draft_records` 将分片写入 Manifest 和全文索引，`ProjectService.sync` 在来源变化后重新计算草案新鲜度，`KnowledgeAPI.search`、`KnowledgeAPI.get` 和 `KnowledgeAPI.context` 负责检索、完整读取和任务上下文优先选择。上下文先选择知识页明确引用的路径和直接符号命中，再补充有界依赖文件；长文档片段先按任务词相关性选择，通用不变量关键词只作次级加权；返回的 `selection_reasons` 用于说明每个证据为何入选。评测扩展知识来源时只计算尚未入选的路径，并限制为两条，兼顾直接证据召回与文件精确率。即使来源新鲜，draft 仍要求开发者读取实时源码，来源变更后标记为 `potentially_stale`。

<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="src/project_knowledge/retrieval.py" -->
<!-- project-kb:source file="src/project_knowledge/codegraph.py" -->
<!-- project-kb:source file="src/project_knowledge/service.py" -->

从 draft 提升到 curated 必须调用 `project-kb propose --draft <feature-id>` 创建稳定 Proposal。创建时再次验证 Feature Guide Schema，收集草案 Markdown、结构化 JSON 与逐陈述源码引用的当前哈希。未审核 Proposal 不修改 curated。审核人使用 `apply --dry-run` 检查 diff 后显式应用，内容只进入该功能的 generated block。目标或任一来源变化都会把旧提案冻结为 `conflicted`，必须重新生成；模型或普通生成命令不能自行升级。

源码同步产生 Semantic Update Queue 项，关联 Proposal 后保留审计关系；纯 Feature Guide/curated 审核同步不重新入队，避免审核结果制造无限语义待办。

0.1.6 最终复核确认：队列只把项目源码或业务配置变更交给后续语义处理，不把 CI、测试、评测和知识文档变化当成功能事实变化。

<!-- project-kb:source file="src/project_knowledge/proposal.py" -->
<!-- project-kb:source file="tests/test_proposal.py" -->
<!-- project-kb:source file="tests/test_semantic.py" -->

<!-- project-kb:generated id="retrieval-guide" -->
功能开发上下文除返回代码符号、影响关系和验证命令外，涉及 watcher 或 Git 分支变更时必须提示读取 watcher_health、branch_aligned、二次哈希结果和结构化日志；跨进程 daemon 尚未实现的结论必须标为 unknown。

涉及配置或客户端集成的功能开发必须先检查 config-v1 Schema 与 migrate dry-run 结果，并把用户扩展字段、客户端所有权标记和知识库保留作为验收不变量；未知用户配置字段在迁移和校验时不能被静默丢弃。Windows 原生 watcher/hooks 尚未实机复验时必须列入 unknowns。

中文功能描述可以通过显式、可审计的短语词元映射召回英文源码标识符；返回的 reference implementation 和 extension point 仍必须指向真实符号，映射本身不能作为业务事实。
长文档知识片段按任务相关行和邻近上下文截取，确保开发指导中的不变量、回滚和验证步骤即使位于文档末尾也能进入有限上下文。
安全不变量、回滚和验证语句在有限上下文中获得明确优先级；该优先级只影响片段选择，不改变来源可信度或自动审核状态。
涉及 Lua/Skynet 功能开发时，context 可以引用生成的入口证据页和只读范围报告；静态启动/派发来源必须带路径与行号，动态服务发现、协议运行时名称和启动命令必须保留在 unknowns。
<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/real_project.py" -->
当项目没有 Lua/Skynet 入口时，入口知识页必须明确显示未检测到，并引用解析器/生成器实现作为来源；不得让空页降低 generated source coverage。Markdown 仅在源码模块相对相关性达到 0.8 时保留该模块页，避免宽泛来源降低 precision。
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
<!-- /project-kb:generated -->

## WP-12A 上下文契约（0.1.29）

`KnowledgeAPI.context()` 暴露有序的 `core_files`、`supporting_files`、`files`、`file_rankings` 和结构化 `ranking_status`。候选在 stale/pending 来源屏蔽后才进入统一生产排序；在 token 预算不足时，先压缩可选诊断，再保留核心证据、精确符号、可用知识和 token-budget withholding 记录。当前 50 条绝对门仍需 clean-source 索引重建后复核，未通过的指标保持未达标。

<!-- project-kb:source file="src/project_knowledge/retrieval.py" -->
<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
