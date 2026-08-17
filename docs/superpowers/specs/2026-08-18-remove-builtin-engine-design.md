# 0.1.30 移除 Builtin Engine 设计

日期：2026-08-18  
目标版本：0.1.30  
工作包：WP-13  
状态：待用户书面复核

## 1. 背景与决策

当前项目虽然已经具备真实 CodeGraph 公共 CLI Adapter，但运行时仍保留两套代码事实来源：

- `.project-kb.yml` 和 `ProjectConfig` 默认选择 `builtin`；
- `BuiltinCodeIndexEngine` 使用 Python AST、Lua 专项解析和通用正则建立本地代码索引；
- `CodeGraphEngine.parse()` 仍委托 `BuiltinCodeIndexEngine` 填充 SQLite 兼容缓存；
- `ProjectService.initialize(dry_run=True)`、`real_project.py` 和部分检索逻辑仍直接调用 builtin；
- 配置 Schema、状态字段、评测和知识文档仍把 builtin 描述为正式引擎。

这使“CodeGraph 是唯一代码事实权威”的产品方向不成立，也使 Adapter 不可用时存在继续依赖本地解析结果的空间。

本工作包采用彻底删除方案：项目只支持真实 CodeGraph Adapter。删除 builtin 实现、配置入口、隐式 fallback、离线解析和依赖它的测试夹具。CodeGraph 不可用或能力不足时明确失败，不使用本地解析、grep 或历史 SQLite 代码事实代替。

## 2. 方案比较

### 2.1 方案 A：只禁用配置入口

保留 `BuiltinCodeIndexEngine`，但不允许用户配置它。改动最小，却会保留 `CodeGraphEngine.parse()`、测试和维护代码中的隐藏 fallback，无法证明生产路径已经脱离 builtin。

结论：不采用。

### 2.2 方案 B：把 builtin 移到测试或迁移工具

生产包不暴露 builtin，但在测试包或迁移脚本中保留解析器。这样能降低测试迁移成本，却仍需长期维护一套不属于产品方向的解析实现，也容易再次被生产代码调用。

结论：不采用。

### 2.3 方案 C：彻底删除，CodeGraph 单一事实源

删除所有 builtin 解析代码。保留引擎协议作为 CodeGraph Adapter 和测试替身的边界；本地 SQLite 仅保存知识、文件快照、提案、验证元数据和查询统计，不再保存由本地解析器产生的代码结构事实。

结论：采用。该方案有明确的兼容性代价，但边界最简单，也最符合用户确认的产品方向。

## 3. 范围与非目标

### 3.1 本工作包范围

- 删除 `BuiltinCodeIndexEngine`、`LuaParser`、Python AST 和通用正则解析实现，以及只服务于这些实现的模型和辅助函数；
- 删除 `CodeGraphEngine` 的 `_builtin_engine()`、`_builtin` 状态和 `parse()` 委托；
- 把 `CodeIndexEngine` 收敛为 CodeGraph 实际支持的公共查询与生命周期契约；
- 重构初始化、同步和重建流程，使本地文件快照来自 CodeGraph 公共 CLI；
- 删除配置、Schema、状态、检索解释和文档中的 builtin 选项或事实来源；
- 将真实项目验证、测试和评测切换到 CodeGraph Adapter 或协议级 fake CLI；
- 更新审计、版本计划、CHANGELOG、版本号和项目知识；
- 删除不再有真实数据来源的生成文档能力，而不是保留空结果或伪造能力。

### 3.2 非目标

- 不实现新的自研解析器；
- 不读取 CodeGraph 私有数据库或未公开文件格式；
- 不把 grep、正则或历史 SQLite 数据包装成 CodeGraph 结果；
- 不在本工作包内降低 WP-12A 的召回率、精确率或成本阈值；
- 不把现有 WP-12A 质量门失败宣称为已解决；
- 不增加第二个运行时引擎或自动降级开关。

## 4. 需求与验收标准

| ID | 需求 | 验收标准 |
| --- | --- | --- |
| BE-001 | 删除 builtin 实现 | 生产代码和测试不再定义、导入或实例化 `BuiltinCodeIndexEngine`、`LuaParser` 或等价本地代码解析器 |
| BE-002 | CodeGraph 是唯一引擎 | `create_engine()` 只创建 `CodeGraphEngine`；新配置默认写入 `engine: codegraph` |
| BE-003 | 删除隐藏 fallback | CodeGraph 缺失、未初始化、超时、命令失败或输出非法时返回结构化错误；不调用本地解析、grep 或历史代码事实 |
| BE-004 | 严格处理旧配置 | 读取 `engine: builtin` 时明确返回 `unsupported_engine` 和迁移提示，不静默改写配置 |
| BE-005 | 本地存储去代码事实化 | 初始化和同步不再调用 `engine.parse()`；文件快照来自 CodeGraph，符号和关系查询实时委托 Adapter |
| BE-006 | 能力声明真实 | status/capabilities 只声明真实 CodeGraph 公共接口已经验证的能力；不支持的路由、入口点或语言专项证据明确移除 |
| BE-007 | 测试脱离 builtin | 单元测试使用协议 fake 或 fake CLI；发布验证使用真实 CodeGraph；不存在以 builtin 通过测试的路径 |
| BE-008 | 文档与知识一致 | 审计、ADR、README、版本计划、生成知识和 CHANGELOG 不再把 builtin 描述为可用或默认引擎 |
| BE-009 | 交付闭环 | 相关测试、全量测试、真实 Adapter 验证、版本验证和知识收尾均有可复现证据 |

## 5. 目标架构

### 5.1 组件边界

`CodeIndexEngine` 保留为外部代码事实提供方的协议，不再承担本地解析职责。协议包含：

- `initialize(root, config)`；
- `sync(root, config, previous=None)`；
- `snapshot(root, config)`；
- `diagnose(root)` / `status()`；
- `search_symbols(...)`；
- `get_source(...)`；
- `trace(...)`；
- `impact(...)`；
- `affected_tests(...)`。

从协议中删除 `parse()` 和 `discover()`。所有文件发现统一通过 `snapshot()` 完成，服务层不得读取或解析源文件内容来推断代码结构。

`CodeGraphEngine` 是唯一生产实现。它只通过 CodeGraph 公共 CLI 工作，不读取 `.codegraph` 私有数据库。

`KnowledgeStore` 继续保存：

- CodeGraph 提供的文件路径、语言、大小、修改时间和内容哈希快照；
- curated、decision、draft 和仍有可靠来源的 generated knowledge；
- 提案、事件、查询统计、Git/验证元数据和 guidance graph。

`KnowledgeStore` 不再把本地解析产生的 symbols、relations、routes 当作当前代码事实。0.1.30 暂时保留这些空表以兼容现有数据库结构，首次迁移时清空全部旧数据；任何运行时查询不得再读取这些表。物理删除空表属于后续独立存储迁移，不是恢复 builtin 能力。自动化测试必须证明迁移会清空旧数据且查询链路不依赖这些表。

### 5.2 数据流

初始化流程：

```text
project-kb init
  -> 验证配置只允许 codegraph
  -> 解析并探测 CodeGraph 公共 CLI
  -> CodeGraph init
  -> CodeGraph snapshot/files
  -> 写入本地文件快照与知识元数据
  -> 生成仍有真实来源的知识文档
```

同步流程：

```text
project-kb sync
  -> CodeGraph sync
  -> 获取新 snapshot
  -> 对比文件哈希和 Git 元数据
  -> 更新文件快照并清除旧代码事实
  -> 刷新 curated/decision/draft 与可验证生成知识
```

查询流程：

```text
context / impact / trace
  -> CodeGraph Engine 获取符号、关系和影响范围
  -> KnowledgeStore 获取已审核知识与验证元数据
  -> 统一排序和预算裁剪
  -> 返回 provenance=codegraph 或 knowledge
```

任何 CodeGraph 调用失败都会终止依赖代码事实的请求。不得继续返回 SQLite 中旧 symbols/relations，也不得自动切换 grep。

### 5.3 生成知识调整

当前项目地图、模块地图、路由、入口点和测试地图部分依赖 builtin 的全量 ParseResult。CodeGraph 1.5 公共 CLI 没有提供完整图导出接口，因此不能用私有数据库补齐。

本工作包按以下规则处理：

- 项目地图保留文件、语言、模块和验证元数据，但删除“Python AST/其他语言正则解析”说明；
- 只有能由 CodeGraph 公共输出或已审核知识直接证明的符号与关系才进入生成知识；
- 无法通过公共接口完整生成的 routes、entrypoints 或 exhaustive module symbol lists 停止生成，并从 manifest 删除；
- 已存在的失去事实来源的 generated 文件在同步时删除；
- curated 和 decision 文档保留，但引用 builtin 的内容必须人工复核并更新；
- 不生成“0 条路由”“0 个入口点”之类可能被误解为真实分析结论的占位文档。

## 6. 配置与迁移

### 6.1 新项目

新建配置固定为：

```yaml
index:
  engine: codegraph
```

配置 Schema 的 engine 枚举只包含 `codegraph`。如果未来只有一个选择，可以保留字段用于显式声明和错误检查；本工作包不删除该字段。

### 6.2 旧项目

发现 `engine: builtin` 时不自动修改文件，返回：

```json
{
  "error": "unsupported_engine",
  "configured_engine": "builtin",
  "supported_engines": ["codegraph"],
  "migration": "set index.engine to codegraph and initialize CodeGraph for this project"
}
```

这样可以避免用户在未安装 CodeGraph 时被静默迁移到不可用状态，也不会继续使用已删除引擎。

仓库自身的 `.project-kb.yml` 在本工作包中显式改为 `codegraph`。由于该改动会使后续知识同步依赖真实 CodeGraph，实施前必须先通过 Adapter 探测与真实集成测试。

### 6.3 本地索引迁移

已有 `.project-kb/index.db` 可能包含 builtin symbols、relations 和 routes。首次使用 0.1.30 同步时必须：

1. 先验证 CodeGraph 可用并成功取得 snapshot；
2. 在同一事务中清除旧代码事实并更新文件快照；
3. 保留 curated、decision、draft、提案和统计数据；
4. 任一步失败时回滚，不留下混合事实源数据库。

## 7. 错误契约

错误必须区分以下原因：

- `unsupported_engine`：配置仍为 builtin 或未知值；
- `cli_missing`：找不到 CodeGraph 命令；
- `project_not_initialized`：CodeGraph 已安装但项目未初始化；
- `command_failed`：命令非零退出、超时或进程异常；
- `invalid_adapter_output`：JSON 非法、字段缺失或路径越界；
- `capability_unavailable`：调用 CodeGraph 公共接口未提供的能力。

`status` 可以在 Adapter 不可用时返回诊断结果；依赖代码事实的命令必须非零退出。错误中应包含安全的命令显示、项目路径、reason code 和下一步建议，但不得包含私有数据库内容或把历史结果标为新鲜。

## 8. 测试策略

实施采用 TDD，先删除能力契约中的 builtin 假设，再修改生产代码。

### 8.1 配置与工厂测试

- 新配置默认 `codegraph`；
- Schema 拒绝 builtin；
- 旧 builtin 配置产生 `unsupported_engine`；
- `create_engine()` 只返回 `CodeGraphEngine`；
- 全仓不存在 builtin 生产符号和分支。

### 8.2 Adapter 与服务测试

- fake CLI 覆盖 init、sync、status、files、query、node、callers、callees、impact 和 affected；
- dry-run 不调用 builtin，也不伪造文件发现；
- 初始化与同步不调用 `parse()`；
- CodeGraph 失败时不读取旧 symbols/relations，不返回部分成功；
- builtin 数据库迁移成功时清除旧代码事实，失败时事务回滚；
- 从引擎协议和能力声明删除路由与入口点；仍处于兼容期的公开调用统一返回 `capability_unavailable`。

### 8.3 检索与评测测试

- hybrid/code 的代码事实 provenance 只能是 `codegraph`；
- Markdown/knowledge 仍可提供已审核知识，但不能冒充代码事实；
- grep 只允许作为显式评测对照策略，不得作为运行时自动 fallback；
- CodeGraph 不可用样本必须记录 Adapter 失败，而不是得到 builtin 或 grep 结果；
- 重新运行 50 条评测并单独报告 WP-12A 既有质量门，不降低阈值。

### 8.4 真实集成与交付测试

至少运行：

```powershell
.venv\Scripts\python.exe scripts\validate_codegraph_adapter.py
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\validate_ci_workflow.py
.venv\Scripts\python.exe -m project_knowledge evaluate evaluation\questions.jsonl --project . --strategy all --thresholds evaluation\thresholds.json --output evaluation\reports\latest.json --quiet
.venv\Scripts\python.exe -m project_knowledge --version
.venv\Scripts\python.exe -m project_knowledge finalize . --check --json
```

评测失败必须如实保留，不能通过删除 CodeGraph 不可用样本、降低阈值或引用陈旧报告完成验收。

## 9. CI 与发布

- 普通单元测试使用仓库内 fake CLI，不要求开发机安装 CodeGraph；
- 发布质量工作流必须执行真实 CodeGraph Adapter 验证；
- CI 无法安装或运行真实 CodeGraph 时发布工作流失败，不能标记为跳过后继续发布；
- 评测必须由当前工作树的本地模块执行，避免 editable package 指向其他 worktree；
- 版本通过 `python scripts/bump_version.py "移除 builtin engine，CodeGraph 成为唯一代码事实源"` 从 0.1.29 递增一次；
- CHANGELOG 记录破坏性变更、旧配置迁移和离线能力删除；
- 不为知识同步再次递增版本。

## 10. 文档与知识复核

必须复核并更新：

- `docs/project-knowledge-system-audit.md` 中 EN-001 至 EN-004、WP-11/12 和 builtin 历史描述；
- `docs/next-version-plan.md`，新增 WP-13 和 BE-001 至 BE-009；
- ADR-0001、ADR-0002、ADR-0003 中 builtin 默认、离线实现和 Lua/Skynet 入口证据决策；
- README、配置示例、MCP/客户端指导和评测说明；
- generated knowledge 中 `BuiltinCodeIndexEngine`、AST、正则解析、routes 和 entrypoints 相关内容。

历史记录可以保留“旧版本曾使用 builtin”的事实，但必须带版本和历史语境；当前架构、当前能力和迁移说明不得再把 builtin 表述为可选项。

## 11. 风险与控制

### 11.1 CodeGraph 公共接口覆盖不足

公共 CLI 当前不提供完整代码图导出。控制方式是缩减本地生成能力并实时查询 CodeGraph，而不是读取私有数据库或恢复本地解析器。

### 11.2 离线能力消失

这是用户确认接受的产品方向变化。配置迁移和错误消息必须明确说明 CodeGraph 是运行前置条件。

### 11.3 旧 SQLite 污染结果

通过一次性事务迁移、查询路径断言和失败回滚测试，确保旧 builtin 代码事实不再被读取。

### 11.4 现有质量门仍失败

WP-12A 当前仍存在 hybrid/code/Markdown 的召回、核心精确率和上下文成本失败。WP-13 不降低阈值，也不因为事实源切换自动宣称这些问题解决。最终报告必须同时列出 builtin 删除验收和检索质量门结果。

### 11.5 测试夹具过度模拟

fake CLI 只验证确定性错误和归一化契约。真实 CodeGraph 1.5 集成验证是发布必需项，不能由 mock 替代。

## 12. 完成定义

只有以下条件全部满足，WP-13 才能标记完成：

- BE-001 至 BE-009 均有正负自动化测试；
- 生产代码和可发布包中不存在 builtin engine、解析器或 fallback；
- 真实 CodeGraph 初始化、同步、符号查询、源码、调用链、影响分析和受影响测试验证通过；
- 旧 builtin 配置与 Adapter 不可用场景均明确失败；
- 本地数据库不再读取或混合旧 builtin 代码事实；
- 全量测试和 CI 结构检查通过；
- 50 条评测使用当前源码运行，结果如实记录；
- 版本仅递增一次，CHANGELOG 与唯一版本源一致；
- 审计、计划、ADR 和 README 已更新；
- generated knowledge 已同步，curated/decision knowledge 的复核结果已明确记录；
- `project-kb finalize --check` 的最终状态及未解决原因被如实报告。
