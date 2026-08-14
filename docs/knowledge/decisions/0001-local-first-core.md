# ADR 0001：本地优先、无依赖的 MVP 核心

状态：已接受

## 背景

系统必须在不上传源代码、不依赖网络访问的前提下完成项目知识的初始化、同步、查询和服务。同时，实现需要具备可替换的代码索引边界，并支持跨客户端 MCP。

## 决策

使用 Python 3.11+ 标准库实现 MVP 核心。可重建的本地索引使用 SQLite WAL；Python 使用 AST 进行高可信度解析；其他语言使用保守的模式匹配；MCP 使用基于 stdio 的逐行 JSON-RPC；共享知识使用版本化 Markdown/JSON。

外部结构引擎统一置于 `CodeIndexEngine` 边界之后。内置引擎是离线实现，并不声称能够完成全面的多语言语义分析；外部 Adapter 未真实安装或实现时必须明确失败，不允许以 builtin 静默冒充已选择的外部引擎。

真实项目验收通过临时只读镜像完成：只复制明确允许索引的文件，初始化产物只存在于临时目录，并比较真实源目录全树元数据快照。评测报告可以进入版本库，但不得包含源代码正文或绝对源路径。

模型能力统一置于 `ModelProvider` 边界后。默认 Provider 为 disabled；Fake 只用于离线测试；HTTP Provider 只有在显式启用网络后才能调用。本机 loopback 可在 local_only 下使用，非本机 endpoint 必须使用 HTTPS、关闭 local_only 并提供固定的源码外发授权短语。外发内容只能来自有文件数和 Token 上限、拒绝高风险路径、完成 Secret 脱敏且具有稳定哈希的 EvidencePack。

模型生成的功能语义不能直接成为人工知识。Feature Guide、Workflow 和 Recipe 先以 `draft/generated` 分片保存；系统必须在缓存和知识写入前验证结构、EvidencePack 成员、项目边界、行号及文件/符号哈希。已有文档只能作为候选证据。提升为 `verified` 必须经过后续受控审核。

受控审核采用本地 Proposal 文件：稳定 ID 由规范化意图计算，目标与可解析来源均保存哈希快照；curated 只允许修改显式 generated block，设计决策只允许新增 ADR 草案。apply 和 reject 保留审核人、时间与理由；目标或来源变化会冻结旧提案，防止把过期模型结论应用到当前代码。

评测基线只在数据集哈希相同时进行汇总回归比较；扩充或修改问题集时必须先通过原有绝对阈值，再冻结新数据集基线。这样既不会把不同问题的均值误判为代码回归，也不能借更换数据集绕过能力下限。

## 结果

CLI 可以立即离线运行且易于安装。在 MVP 中，Python 项目获得的静态事实强于其他语言。替换或增强索引引擎不会改变 CLI、知识模型或 MCP 契约。

重建可丢弃索引时保留人工知识和 ADR 的验证基线。只有显式编辑文档才会推进其验证哈希。

本地结构化清单和变更事件在落盘前执行无第三方依赖的运行时 Schema 验证。内容新鲜度与 Git 提交对齐分别报告，避免把“内容相同但 HEAD 已变化”误报为已经在当前提交上验证。

软件包版本以 `src/project_knowledge/__init__.py` 为唯一来源，构建元数据动态读取该值。后续每批修改或新增内容只递增一次补丁版本，并由版本工具同步写入变更日志，避免 CLI、MCP 与发布包的版本发生漂移。

<!-- project-kb:source file="pyproject.toml" -->
<!-- project-kb:source file="src/project_knowledge/versioning.py" -->
<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/mcp.py" -->
<!-- project-kb:source file="src/project_knowledge/real_project.py" -->
<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
<!-- project-kb:source file="src/project_knowledge/evidence.py" -->
<!-- project-kb:source file="src/project_knowledge/provider.py" -->
<!-- project-kb:source file="src/project_knowledge/semantic.py" -->
<!-- project-kb:source file="src/project_knowledge/proposal.py" -->
<!-- project-kb:source file="src/project_knowledge/schemas.py" -->
<!-- project-kb:source file="docs/project-knowledge-system-design.md" -->

## WP-01/WP-02 复核（2026-08-07）

已复核当前实现：BuiltinCodeIndexEngine 已提供统一初始化、同步、符号检索、源码读取、追踪、影响分析和受影响测试查询契约；Lua/Skynet、SQL、配置解析作为保守结构证据接入。0.1.27 的 CodeGraph Adapter 已通过真实 1.5 公共 CLI 夹具验证；配置或运行环境不可用时仍明确失败，且绝不回退 builtin。正式边界见 ADR-0002。

<!-- project-kb:source file="src/project_knowledge/codegraph.py" -->
<!-- project-kb:source file="scripts/validate_codegraph_adapter.py" -->

## WP-08 兼容性复核（2026-08-07）

本地优先边界保持不变：config-v1 Schema 和 migrate 只负责本地配置演进；Claude、Cursor、Gemini 适配只写入受控标记，不拥有知识数据。版本工具以核心 __version__ 为来源，并同步 CHANGELOG 与 Codex 插件清单。wheel/sdist 0.1.10 已离线构建验证；Windows 原生生命周期仍需独立 CI。

## WP-11 检索复核（2026-08-14）

评测仍先执行冻结的绝对质量门。hybrid 的知识来源扩展只补充尚未入选的路径，并限制为两条；该约束用于避免重复来源占位和无关文件膨胀，不改变知识可信度、来源追踪或实时源码核验边界。

<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
<!-- project-kb:source file="evaluation/thresholds.json" -->
