# 架构

Project Knowledge CLI 是一个本地优先的 Python 应用，CLI 和 MCP 适配器共用同一套核心能力。运行时不依赖第三方软件包。

## 职责边界

- `ProjectService` 负责初始化、同步、原子重建、文件监视、健康检查以及由标记边界保护的客户端集成。
- `CodeIndexEngine` 是可替换的结构索引边界。内置实现使用 Python AST 提取和保守的多语言模式匹配。
- `KnowledgeStore` 负责私有 SQLite 架构、WAL 行为、一致性事务、全文检索和查询统计，其数据表不属于公共 API。
- `KnowledgeGenerator` 负责生成 Markdown、清单记录、发现人工文档中的来源标记以及维护新鲜度状态，绝不覆盖现有人工维护文档。面向人的生成标题、说明、表头和索引状态默认使用中文，底层记录 ID 与接口枚举保持稳定。
- 原子重建会将人工知识和决策记录的哈希基线带入替换数据库。仅来源发生变化时，过期状态会跨重建保留；编辑人工维护正文是接受当前哈希的显式验证事件。
- 状态检查将完整文件哈希、Git 状态和 SQLite 健康状态作为独立读取操作执行。文件系统与 Git 工作可并行处理，但不会削弱新鲜度检查。
- `KnowledgeAPI` 负责排序、Token 预算、待同步来源屏蔽、任务上下文和影响范围组装。
- 除非任务明确要求项目概览，否则任务上下文不包含宽泛的项目地图；在图锚点之前最多返回四个已排序的知识页面。
- `MCPServer` 和参数解析器是这些服务之上的轻量适配器。
- `plugins/project-knowledge` 仅包含 Codex 集成；核心功能不依赖 Codex 也可以使用。

## 数据流

来源发现和解析结果写入事务型 SQLite 索引。确定性记录渲染到 `docs/knowledge/generated`，人工知识保存在 `docs/knowledge/curated` 和 ADR 中。MCP 查询将三类内容与图锚点、实时新鲜度检查组合后返回。

<!-- project-kb:source file="src/project_knowledge/service.py" -->
<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/store.py" -->
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="src/project_knowledge/retrieval.py" -->
<!-- project-kb:source file="src/project_knowledge/mcp.py" -->
