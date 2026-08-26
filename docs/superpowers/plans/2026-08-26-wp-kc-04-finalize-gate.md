# WP-KC-04 Finalize 门禁实施计划

## 目标

让 `finalize` / `finalize --check` 同时验证 Git、CodeGraph、初始化批次和 guidance 闭环，返回可解释的阻断原因与下一动作。

## 门禁条件

- 最新 guidance run 为 `complete`，所有批次成功覆盖当前文件。
- 至少一个类别；每个类别都有带证据的 methodology 和 project guidance 正式版本。
- 不存在 `incomplete` 草稿；`awaiting_confirmation` 草稿不阻塞基础 Ready。
- 不存在 pending guidance change，且 guidance baseline 等于当前 CodeGraph snapshot。
- knowledge 没有 stale/conflicted 记录，Git/index/CodeGraph verification 对齐。

## 交付

- `FinalizationService` 输出 `gate_blockers`，每项包含 `code/detail/next_action`。
- `--check` 保持只读；需要同步、提交或审核时返回非零结果。
- 新增无 guidance、完整 guidance、草稿、pending change 和 baseline mismatch 测试。
- 更新审计、CHANGELOG、版本和 generated knowledge。
