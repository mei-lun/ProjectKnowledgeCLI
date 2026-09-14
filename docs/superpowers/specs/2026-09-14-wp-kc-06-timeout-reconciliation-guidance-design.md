# WP-KC-06 超时、Git 对账与 Guidance 初始化修复设计

日期：2026-09-14  
任务 ID：`wp-kc-06-timeout-reconcile-20260914`  
需求：`KC6-001`、`KC6-003`、`KC6-006`、`KC6-007`

## 1. 背景与已验证现象

当前仓库的 Project Knowledge 基础索引和 CodeGraph 1.5.0 均可用，但 guidance workflow 尚未初始化，项目级 Codex MCP 配置也不存在。受控诊断得到以下结果：

- 首次调用 `knowledge_changes` 时，`IncrementalWorkflow` 把缺失的 `guidance_snapshot` 当成空基线，逐个处理当前快照内全部 113 个文件。调用耗时约 81.4 秒，返回约 1.52 MB JSON，并写入一个覆盖全项目的 pending change。
- 同一 pending change 再次读取只需约 0.26 秒，证明主要成本来自首次全量源码、符号和影响分析，而不是数据库读取。
- `knowledge_search` 独立调用约 0.59 秒。MCP stdio 服务串行处理请求，因此一个尚未结束的 `knowledge_changes` 会让后续搜索排队，表现为搜索超时。
- `ProjectService.status()` 只要最后一个 Git 事件是 `git_event_failed` 就持续返回 `reconciliation_required=true`，即使 CodeGraph 已恢复、内容已新鲜且 Git/索引已经对齐。只有后续成功 Git 事件覆盖日志尾项时该状态才会消失。
- `.codex/config.toml` 缺失，`doctor` 报告项目级 Codex integration 未配置。当前会话因而不能直接调用 `knowledge_*` MCP 工具。

本设计不引入后台 daemon、异步任务队列或第二套代码事实引擎。

## 2. 目标与非目标

### 2.1 目标

1. Guidance 尚未形成可处理增量的基线时，`knowledge_changes` 必须快速返回初始化动作，不得把整个项目误判为新增变化。
2. 已初始化项目的变化分析必须有明确工作量边界；超出边界时返回可诊断、可继续的人工复核结果，不产生无界响应。
3. 一个变化调用不得再因未初始化全量扫描而阻塞随后正常的 `knowledge_search`。
4. 历史 hook 失败在运行环境恢复且当前事实已对齐后不再保持活动告警，同时保留失败事件用于审计。
5. 修复项目级 Codex 接入，并把本仓库 guidance workflow 推进到完整草稿已生成、等待用户审核的状态。

### 2.2 非目标

- 不并行化 MCP stdio 请求，不实现请求取消协议或持久任务调度。
- 不自动确认分类、方法论或项目指导草稿。
- 不删除历史 hook 失败日志。
- 不通过增大客户端超时掩盖无界工作。
- 不把未审核草稿作为 verified guidance 注入默认上下文。

## 3. 接口与状态设计

### 3.1 初始化前置门禁

`IncrementalWorkflow.changes()` 在调用 CodeGraph `snapshot/source/query/impact` 之前打开 KnowledgeStore，并检查：

- 是否存在非 `run-incr-*` 的完整初始化运行；
- 是否存在 `guidance_snapshot` 基线；
- 是否仍处于初始化扫描、草稿生成或审核阶段。

只要基线不存在，就返回稳定结果：

```json
{
  "status": "initialization_required",
  "change_id": null,
  "changed_files": [],
  "affected_categories": [],
  "facts": [],
  "next_action": "start_initialization",
  "next_tool": "knowledge_initialization_start",
  "arguments": {"projectPath": "<absolute-project-path>"}
}
```

该分支不得创建 guidance run、change 或 draft，也不得调用 CodeGraph。现有合法增量基线行为保持不变。

初始化开始时，如果数据库中存在以 `base_snapshot_id=none` 创建的未处理增量变化，且项目从未建立 guidance 基线，则把这些记录标记为被本次初始化 superseded。这样可恢复旧版本或诊断调用已经产生的伪全量变化，同时保留审计记录。

### 3.2 增量分析边界

对已有基线的真实增量分析设置确定性上限：

- 单次详细读取的 changed file 数量有固定上限；
- 每个文件只保留有限符号和影响关系，复用现有 CodeGraph request scope 缓存；
- facts 返回源码摘要或有限行数，不返回无界完整内容；
- 超出上限时公开 `truncated=true`、未分析文件列表和 `next_action=manual_review`，不得把未知范围误报为无需更新。

上限作为模块常量实现并由正负测试固定，不增加用户配置面。正常的小规模增量仍返回原有生成动作。

### 3.3 搜索可用性

不改动搜索排序和召回语义。通过初始化门禁消除本次已验证的队首阻塞来源，并增加 MCP 顺序回归：先调用未初始化的 `knowledge_changes`，再调用 `knowledge_search`，两者均在测试预算内完成，且搜索结果契约不变。

同时限制 workflow 工具返回给审计层的超大 payload：审计继续记录状态、数量、截断信息和关联 ID，但不重复持久化大段源码正文。MCP 实际调用结果仍遵循工具契约。

### 3.4 Git 对账恢复

`last_git_event` 继续返回最后一次 completed/failed 事件，不篡改历史。活动告警改为依据当前可验证状态：

- Git/索引未对齐时始终 `reconciliation_required=true`；
- 最后事件失败且 CodeGraph 仍不可用、内容不新鲜或验证未对齐时为 `true`；
- 最后事件虽然失败，但 CodeGraph 已可用、内容新鲜且验证已对齐时为 `false`，并返回恢复原因供 doctor/status 展示。

MCP 连接补偿仍执行同步。补偿成功后通过上述当前状态自然消除活动告警，不删除失败日志；补偿失败继续保留结构化错误。

## 4. Guidance 初始化与项目接入

实现和测试通过后执行项目级 `project-kb install`，生成受控 `.codex/config.toml` 并复验 MCP `initialize`、`tools/list`、`knowledge_status`、`knowledge_context` 和 `knowledge_search`。

随后按 `knowledge_initialization_start` 返回的批次顺序处理：

1. 对每个 batch 读取其列出的实际源码文件；
2. 提交完整且 hash 可验证的类别候选和 `analyzedFiles`；
3. 保存类别目录草稿；
4. 按稳定类别顺序保存每类 methodology 和 guidance 草稿；
5. 所有 required 草稿落盘后停止，不调用 `knowledge_draft_confirm`。

最终报告每个草稿路径和 content hash，等待用户明确审核。正式 guidance 在用户确认前仍不可用，这是审核边界而不是故障。

## 5. 测试优先实施顺序

1. 在 `tests/test_guidance_incremental.py` 增加未初始化正负样本，断言零 CodeGraph 调用、零 pending 写入以及精确 next tool/arguments。
2. 增加旧伪全量 pending change 被初始化 supersede 的恢复样本。
3. 增加增量文件/符号/影响上限样本，覆盖正常、截断和仅删除场景。
4. 在 MCP 测试中增加 `knowledge_changes` 后立即搜索的顺序调用样本，并验证 workflow 审计摘要不包含源码正文。
5. 在 `tests/test_watch_wp07.py` 增加历史失败后当前健康、仍不健康、后续成功事件三种对账状态。
6. 先运行受影响测试，再运行完整 Python 和 Node 套件及真实 CodeGraph/MCP 验证。

关键验收：

- 未初始化 `knowledge_changes` 在 1 秒级内返回且响应保持为小型结构化对象；
- 紧随其后的 `knowledge_search` 不被阻塞；
- 已初始化小增量行为和草稿审核门不回归；
- 历史失败可追溯但不产生虚假的活动告警；
- 新 Codex 会话能列出并调用全部 knowledge workflow 工具；
- 本仓库初始化产生完整待审草稿，不自动发布。

## 6. 交付与知识同步

本批只递增一次补丁版本，使用：

```powershell
python scripts/bump_version.py "修复知识工具超时、Git 对账恢复并初始化指导工作流"
```

随后验证 `python -m project_knowledge --version` 和 CHANGELOG 对应记录。源码与文档修改完成后运行：

```powershell
project-kb sync --task-summary "修复知识工具超时、Git 对账恢复并初始化指导工作流"
```

最后以任务 ID `wp-kc-06-timeout-reconcile-20260914` 调用 `knowledge_task_complete`，继续执行返回的知识更新动作，直到状态为 `generated`、`no_update`、`skipped` 或明确失败/人工复核。生成草稿和正式确认保持分离。

## 7. 风险与回退

- 初始化期间源码变化会使 snapshot 校验失败；此时保留失败原因并显式重启，不提交旧事实。
- 增量上限可能要求人工复核大型变更；这是公开的不完整状态，优于超时或伪造“无需更新”。
- 项目级 Codex 配置只更新 project-kb 自有块，保留用户配置；验证失败时保留诊断信息，不宣称安装成功。
- 当前诊断生成的伪全量 pending change 不直接删除，由初始化 supersede 规则以可审计方式收口。
