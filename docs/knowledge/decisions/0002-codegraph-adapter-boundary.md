# ADR-0002：CodeGraph Adapter 边界与本地替代方案

0.1.30 复核补充：Adapter 对外统一使用 `项目路径::qualifiedName/name`，CodeGraph 内部哈希 ID 只用于单次查询关联，不进入公共知识契约。

- 状态：已接受
- 来源提案：kp-12a589734edb3c2d
- 创建审核人：codex
- 日期：2026-08-07
- 最近复核：2026-08-14
- 当前版本：0.1.27
- 决策者：项目维护者

## 背景

审计要求 CodeGraph 与内置索引引擎具有相同公共契约；这条失败边界的原因始终是：不允许以 builtin 静默冒充已选择的外部引擎。0.1.8 建立边界时，仓库还没有可复现、可验证的真实 CodeGraph Adapter，因此当时只能明确返回 `adapter_unavailable`。

0.1.27 已通过 CodeGraph 1.5 公共 CLI 完成真实适配，并用临时项目验证初始化、文件、符号、追踪、影响和受影响测试查询。历史上的“真实 Adapter 不可用”不再代表当前状态。

## 决策

1. `CodeIndexEngine` 的公共契约固定为 `initialize`、`sync`、`search_symbols`、`get_source`、`trace`、`impact`、`affected_tests`、`capabilities` 与 `health`。
2. BuiltinCodeIndexEngine 和本地 parser 不再属于产品运行时；历史 SQLite 符号、关系和路由表仅为迁移兼容保留，CodeGraph 是唯一代码事实来源。
3. `engine: codegraph` 只调用 CodeGraph 公共 CLI/API，不读取其私有数据库，也不回退到 builtin；不允许以 builtin 静默冒充已选择的外部引擎。CLI 不存在、项目未初始化或响应不满足契约时必须显式失败。
4. CodeGraph 响应在 Adapter 边界内规范化。内部不透明符号 ID 不暴露给后续公共查询；追踪和影响查询优先使用 `name`/`qualifiedName` 等公共引用。受影响测试查询使用 CodeGraph 支持的公共过滤表达式。
5. `KnowledgeAPI` 通过当前选择的引擎获得实时符号和关系事实。SQLite 是知识存储和兼容缓存，不能在 `engine=codegraph` 时成为结构事实的唯一来源。
6. 业务开发指导必须把静态结构事实与运行时语义分开：动态分派、反射、依赖注入、Lua metatable 和协议运行时名称必须标为待验证，不得生成确定性结论。

## 验收证据

- `scripts/validate_codegraph_adapter.py` 在临时四文件项目中验证真实 CodeGraph 1.5.0；
- `tests/test_codegraph.py` 覆盖命令解析、响应规范化、错误和不回退边界；
- `tests/test_codegraph_validation.py` 覆盖验证夹具和源仓库不受污染；
- `tests/test_retrieval_wp06.py` 覆盖 SQLite 结构缓存为空时的 CodeGraph 主链路；
- `evaluation/reports/latest.json` 独立报告 codegraph 策略的可用性，不用 builtin 指标冒充。

<!-- project-kb:source file="src/project_knowledge/codegraph.py" -->
<!-- project-kb:source file="src/project_knowledge/retrieval.py" -->
<!-- project-kb:source file="scripts/validate_codegraph_adapter.py" -->
<!-- project-kb:source file="tests/test_codegraph.py" -->
<!-- project-kb:source file="tests/test_codegraph_validation.py" -->

## 0.1.33 CodeGraph 边界复核

向量索引不改变 CodeGraph Adapter 的权威边界。向量只能排序知识候选，不能生成符号、路径、调用关系或影响分析事实；CodeGraph 不可用时向量层也不得伪造结构证据。

## 影响

运行项目必须安装并初始化 CodeGraph；CLI 或项目不可用时明确返回结构化错误，不执行本地解析回退。知识文档和检索结果必须标注 CodeGraph 事实来源。

0.1.27 最终复核：`KnowledgeAPI` 的长文档片段相关性调整只影响证据文本排序，不改变 CodeGraph 实时事实来源、公共 CLI 边界或禁止回退 builtin 的决策。
