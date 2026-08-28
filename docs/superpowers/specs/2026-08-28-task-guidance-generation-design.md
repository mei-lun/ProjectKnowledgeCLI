# 任务确认驱动的知识指导生成设计

## 1. 背景与目标

当前开发指导工作流以一次初始化运行作为主要入口：客户端完成全量扫描后，依次生成类别目录、方法论和项目事实指导草稿。现有流程可以审核草稿，也可以在任务命中待审草稿时返回审核门，但不能表达用户选择生成哪些类别，也不能把“本轮功能已完成”与指导沉淀绑定。

本设计把指导生成统一为三种入口：

1. 用户主动要求生成时，先返回类别目录，用户选择类别后再生成对应指导。
2. 开发前任务上下文缺少正式指导时，客户端询问用户是否先生成命中类别的指导。
3. 用户确认本轮任务完成后，客户端基于本轮对话上下文和实际代码改动自动生成受影响类别的待审核更新草稿。

自动生成只创建 `awaiting_confirmation` 草稿，不直接升级正式知识。Git 只提供代码事实和同步信号，不作为生成决策入口。

## 2. 范围与非目标

### 范围

- 单客户端、单本地知识库。
- 类别/模块级指导作为长期资产；具体功能作为类别指导中的示例、Recipe 和验证记录。
- 任务完成收口、目录选择、开发前询问和 post-task hook 兜底。
- 复用现有 `guidance_drafts`、`knowledge_draft_save`、`knowledge_draft_confirm` 和 CodeGraph 证据校验。

### 非目标

- 不让 Git hook 直接调用大模型。
- 不新增后台常驻 AI broker、跨客户端并发协调或远程知识服务。
- 不自动确认语义草稿，不静默改写人工维护的 curated/decision 内容。
- 不把每个功能拆成永久独立的知识页面。

## 3. 核心状态模型

新增任务收口记录（建议落在 `task_completions` 表，具体命名可在实现阶段按现有 schema 约定调整）：

| 字段 | 含义 |
| --- | --- |
| `task_id` | 对话/任务唯一 ID，作为幂等键 |
| `project_root` | 项目根目录 |
| `summary` | 用户确认后的功能摘要 |
| `changed_files` | 本轮实际修改或删除的文件 |
| `changed_symbols` | 可选的稳定符号 ID |
| `tests` | 执行过的测试命令及结果 |
| `base_snapshot_id` | 任务开始时的 CodeGraph 快照 |
| `final_snapshot_id` | 收口时确认的 CodeGraph 快照 |
| `user_confirmed` | 是否收到用户明确完成确认 |
| `generation_status` | `pending`、`generated`、`skipped` 或 `failed` |
| `affected_categories` | 影响分析得到的类别 ID，稳定排序 |
| `skip_reason` / `error` | 跳过或失败原因 |
| `created_at` / `updated_at` | 审计时间 |

同一个 `task_id` 重复收口时返回已有结果，不创建第二条记录或第二份草稿。任务记录和草稿记录分离，任务可以完成而草稿仍待审核。

## 4. 三条用户流程

### 4.1 主动生成目录

客户端调用目录规划入口（建议命名 `knowledge_guidance_plan`）。服务端基于当前 CodeGraph 快照和已有正式指导返回候选目录，每项包含：

- `category_id`、名称和职责；
- 证据文件及 hash；
- 相关模块、依赖和预计收益；
- `confidence` 与目录版本。

用户选择一个或多个类别后，客户端仅为所选类别生成 `methodology` 和 `guidance` 草稿，按“方法论 → 项目事实指导”顺序保存。未选择类别不创建占位草稿，也不改变正式知识状态。

### 4.2 开发前询问

`knowledge_context(task)` 保持现有上下文返回，并新增 `guidance_offer`：

```json
{
  "reason": "missing|draft_only|stale|sufficient",
  "category_candidates": ["login"],
  "recommended_action": "generate|use_existing|review_draft|skip"
}
```

只有 `missing`、`draft_only` 或明确过期时，客户端才向用户询问。用户同意后，生成范围限定为命中类别；用户拒绝时，在当前任务记录 `skipped` 和原因，后续同一任务不再重复询问。

### 4.3 用户确认完成后自动生成

用户在对话中确认“本次任务完成”后，客户端调用 `knowledge_task_complete`，传入：

- `task_id`、任务摘要和用户确认信息；
- 实际改动文件、符号和测试结果；
- 可选的本轮上下文摘要。

服务端执行一次同步和 CodeGraph 影响分析，锁定 `final_snapshot_id`，返回受影响类别、证据包和下一动作。客户端在同一轮上下文中逐类别生成更新草稿并调用 `knowledge_draft_save`。事实未改变指导结构时可生成轻量事实更新；流程、扩展点或不变量发生变化时生成完整指导草稿。

## 5. 接口与职责

### `knowledge_task_complete`

写入任务收口记录，执行同步、快照锁定和影响分析。接口必须幂等；未确认完成时拒绝生成，缺少任务摘要或改动证据时返回校验错误。返回：

- `task_id`、`generation_status`；
- `final_snapshot_id`；
- `affected_categories`；
- 每个类别的证据包和推荐草稿类型；
- `next_action`（`generate_guidance_draft`、`review_drafts`、`retry_task_completion` 或 `none`）。

### `knowledge_guidance_plan`

只负责返回类别目录和当前正式/待审状态，不保存正文。若客户端提交所选类别，服务端校验目录版本和证据快照后返回确定的生成顺序。

### `knowledge_context`

在不把待审正文注入上下文的前提下，返回 `guidance_offer` 和待审核草稿摘要。正式指导仍按当前新鲜度和可信度规则提供。

### `knowledge_status`

增加任务收口视图：最近任务、`pending_generation` 数量、失败/跳过原因和下一动作。状态优先级必须保证同一时刻只有一个主 `next_action`。

### `sync_after_task.py`

钩子继续先执行 `project-kb sync`。随后检查本轮是否已有 `knowledge_task_complete` 记录：

- 有：只补做状态和快照对齐检查，避免重复生成；
- 无：登记 `pending` 兜底事件，供客户端在任务结束流程中补交收口；
- 不调用大模型，不覆盖任务摘要或用户选择。

## 6. 数据流与一致性

```text
用户确认完成
  -> knowledge_task_complete
  -> sync CodeGraph + impact + lock final snapshot
  -> return affected categories and evidence packs
  -> client generates methodology/guidance drafts
  -> knowledge_draft_save (awaiting_confirmation)
  -> user review
  -> knowledge_draft_confirm (formal version)
```

生成期间如果 CodeGraph 快照变化，任务标记 `failed` 或 `retry_required`，当前草稿不得进入正式版本；重试必须重新锁定快照并重新校验证据。多类别按稳定顺序推进，部分完成不会提前推进 guidance baseline。用户跳过时不创建草稿，但保留原因和任务上下文，便于后续审计和避免重复询问。

## 7. 测试与验收

实现前先补以下正负样本和回归测试：

1. `knowledge_task_complete` 首次调用、重复调用和缺少确认字段。
2. 单类别、多类别影响分析及稳定处理顺序。
3. 目录生成后只选择部分类别，未选择类别不产生草稿。
4. `knowledge_context` 对 `missing`、`draft_only`、`stale`、`sufficient` 的询问门行为。
5. 任务完成后自动生成草稿，草稿仍需确认才能成为正式版本。
6. 生成期间 snapshot 变化、证据 hash 不匹配、某类别失败和重试。
7. 用户跳过后的不重复询问，以及 post-task hook 无收口记录时的兜底登记。
8. MCP schema、状态优先级、检索不注入待审正文和完整端到端流程。

验收条件：从任务确认到待审核草稿可在同一轮客户端上下文完成；重复收口不重复写入；任何未审核内容都不会被当作正式指导；hook 失败不会破坏用户的 Git 或任务流程；生成失败原因、证据和重试入口均可通过 `knowledge_status` 查看。

## 8. 实施拆分建议

建议作为新的工作包 `WP-KC-05` 实施，顺序为：

1. 先补 schema、状态机和 `knowledge_task_complete` 的单元/MCP 测试；
2. 实现任务收口、影响分析和幂等存储；
3. 改造目录规划和按选择生成；
4. 扩展 `knowledge_context`/`knowledge_status` 的询问和待生成状态；
5. 更新 post-task hook 兜底；
6. 补端到端测试、README、审计报告、版本和知识同步。

本设计不要求在实施前新增模型 Provider；模型仍由 AI 客户端负责调用，PKS 负责事实、状态、证据校验和草稿生命周期。
