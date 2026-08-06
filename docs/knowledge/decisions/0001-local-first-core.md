# ADR 0001：本地优先、无依赖的 MVP 核心

状态：已接受

## 背景

系统必须在不上传源代码、不依赖网络访问的前提下完成项目知识的初始化、同步、查询和服务。同时，实现需要具备可替换的代码索引边界，并支持跨客户端 MCP。

## 决策

使用 Python 3.11+ 标准库实现 MVP 核心。可重建的本地索引使用 SQLite WAL；Python 使用 AST 进行高可信度解析；其他语言使用保守的模式匹配；MCP 使用基于 stdio 的逐行 JSON-RPC；共享知识使用版本化 Markdown/JSON。

外部结构引擎统一置于 `CodeIndexEngine` 边界之后。内置引擎是离线后备方案，并不声称能够完成全面的多语言语义分析。

## 结果

CLI 可以立即离线运行且易于安装。在 MVP 中，Python 项目获得的静态事实强于其他语言。替换或增强索引引擎不会改变 CLI、知识模型或 MCP 契约。

重建可丢弃索引时保留人工知识和 ADR 的验证基线。只有显式编辑文档才会推进其验证哈希。

软件包版本以 `src/project_knowledge/__init__.py` 为唯一来源，构建元数据动态读取该值。后续每批修改或新增内容只递增一次补丁版本，并由版本工具同步写入变更日志，避免 CLI、MCP 与发布包的版本发生漂移。

<!-- project-kb:source file="pyproject.toml" -->
<!-- project-kb:source file="src/project_knowledge/__init__.py" -->
<!-- project-kb:source file="src/project_knowledge/versioning.py" -->
<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/mcp.py" -->
<!-- project-kb:source file="docs/project-knowledge-system-design.md" -->
