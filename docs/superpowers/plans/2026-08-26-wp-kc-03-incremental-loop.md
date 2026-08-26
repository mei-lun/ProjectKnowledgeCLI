# WP-KC-03 增量影响闭环实施计划

## 目标

在单客户端、单知识库场景下，把 CodeGraph snapshot 变化转成可顺序消费的 guidance change：每次只处理一个受影响类别；fact 级更新自动完成，guidance/category 级更新生成现有审核草稿；全部类别完成后才推进 guidance baseline。

## 范围

- 复用现有 `IncrementalWorkflow`、`GuidanceChange` 和草稿确认流程。
- payload 固定保存 `pendingCategories`、`completedCategories`、`categoryLevels`。
- 不增加后台任务、并发协调、迁移、恢复或新的模型供应商。
- 已有 semantic proposal queue 仅在未进入 guidance baseline 管理时保留；进入闭环后由 `knowledge_changes` 消费。

## 验收

1. 单类别 fact 更新保持正文不变，校验当前快照证据并推进 baseline。
2. 多类别变化只能按 pending 顺序处理；完成第一个类别时 baseline 不变。
3. guidance/category 更新生成审核草稿，拒绝或快照变化不会标记 change 完成。
4. 无类别命中不能静默推进 baseline，必须保留待分类变化。
5. `knowledge_status` 在 pending change 时唯一返回 `inspect_changes`，并输出类别进度。
6. guidance baseline 管理的项目执行 `sync` 不再生成无人消费的 semantic update queue。

## 验证顺序

- 先补单类别、双类别、越序、拒绝和无类别负例。
- 实现后运行增量、草稿、MCP、状态和 service sync 回归。
- 更新审计、CHANGELOG、版本和 generated knowledge。
- WP-KC-03 完成后再进入 WP-KC-04 finalize 门禁。
