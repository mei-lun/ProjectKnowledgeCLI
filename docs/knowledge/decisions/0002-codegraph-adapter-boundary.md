# ADR-0002：CodeGraph Adapter 边界与本地替代方案

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
2. `BuiltinCodeIndexEngine` 继续作为默认、离线、可复现的正式实现；它提供 Python AST、Lua/Skynet 专项证据以及保守的多语言启发式能力。
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

## 影响

默认安装仍可完全离线运行；启用 CodeGraph 的项目获得更精确的多语言结构事实，并承担外部 CLI 的安装和项目初始化要求。两种引擎能力必须在状态与诊断输出中明确区分。

0.1.27 最终复核：`KnowledgeAPI` 的长文档片段相关性调整只影响证据文本排序，不改变 CodeGraph 实时事实来源、公共 CLI 边界或禁止回退 builtin 的决策。
