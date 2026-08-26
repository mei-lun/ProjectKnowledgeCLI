# 单 AI 客户端知识闭环设计

日期：2026-08-26  
状态：等待书面设计复核  
工作包：WP-KC-01～04

## 1. 背景与目标

Project Knowledge CLI 已经提供 CodeGraph 事实索引、确定性 generated knowledge、分批初始化、草稿审核和增量更新工具，但这些能力依赖 AI 客户端了解调用顺序。`knowledge_status` 能暴露局部状态，却不能稳定回答“下一步调用哪个工具”，初始化完成和增量 baseline 推进也缺少完整性门禁。

本设计面向当前真实使用方式：一个本地项目、一个知识库、一个 MCP AI 客户端顺序工作。目标不是建设通用工作流引擎，而是让 AI 只凭 `knowledge_status` 返回的下一动作，使用现有 MCP 工具完成以下闭环：

1. 首次分批分析全部项目文件；
2. 用户审核分类、方法论和项目指导；
3. 正式知识进入 KnowledgeStore；
4. 后续仅分析变化代码和必要影响范围；
5. 一级事实变化自动更新，二级指导和三级分类变化重新审核；
6. 所有条件满足后返回可验证的 `ready`。

## 2. 适用边界

### 2.1 本期范围

- 单 AI 客户端串行调用 MCP。
- 单项目、单 `.project-kb` 知识库。
- 开发期允许删除并重新初始化知识库。
- 复用现有 guidance run、batch、category、draft、version 和 change 数据。
- `knowledge_status` 只读计算状态和下一动作。
- CodeGraph 继续作为代码事实权威。
- 用户继续通过 Markdown 草稿和正文 hash 完成审核。

### 2.2 明确非目标

- 不迁移当前数据库、草稿、版本或 semantic update queue。
- 不保证旧 Schema 兼容；实现后使用干净知识库重新初始化。
- 不支持多个 AI 客户端并发维护同一知识库。
- 不新增并行分类、任务抢占、租约或分布式锁。
- 不提供终端中断后的精确恢复；中断可重新执行当前轮次。
- 不新增后台 daemon、通用调度器或通用重试平台。
- 不在 PKS 内调用大模型。
- 不新增通用 WorkItem、WorkResolution 或独立审核子系统。
- 不删除现有锁实现，但不为本期扩展并发语义。

## 3. 职责边界

| 参与者 | 职责 |
| --- | --- |
| CodeGraph | 维护文件、符号、调用关系、影响范围和源码事实 |
| PKS | 保存状态、校验快照和证据、生成草稿、确认正式版本、计算下一动作和 Ready 门禁 |
| MCP AI 客户端 | 按下一动作读取事实、分析所有批次、分类变化、撰写草稿并提交结构化结果 |
| 用户 | 审核、修改、确认或拒绝 Markdown 草稿 |

AI 不直接写 SQLite，也不直接覆盖正式指导。PKS 不判断自然语言语义，只校验结构、来源、快照和状态转换。

## 4. 总体流程

```text
knowledge_status
    │
    ├─ next_action=start_initialization ──> knowledge_initialization_start
    ├─ next_action=analyze_next_batch ───> next + AI 分析 + submit
    ├─ next_action=create_category_draft ─> knowledge_draft_save
    ├─ next_action=create_methodology ────> knowledge_draft_save
    ├─ next_action=create_guidance ───────> knowledge_draft_save
    ├─ next_action=await_user_review ─────> 停止并展示草稿路径/hash
    ├─ next_action=inspect_changes ───────> knowledge_changes
    ├─ next_action=classify_change ───────> knowledge_update_submit
    └─ next_action=none, state=ready
```

AI 在每次写入工具成功后重新调用 `knowledge_status`。循环只在 `await_user_review`、`ready` 或 `failed` 停止。

## 5. WP-KC-01：状态与下一动作

### 5.1 需求

| ID | 要求 |
| --- | --- |
| KC-NEXT-001 | `knowledge_status.guidance_workflow` 返回稳定的 `state` 和 `next_action` |
| KC-NEXT-002 | 下一动作完全由现有存储状态和当前 CodeGraph snapshot 只读计算 |
| KC-NEXT-003 | 每个非终态恰好返回一个可执行动作 |
| KC-NEXT-004 | 托管 AGENTS 指示 AI 按 status 返回值循环，而不是依赖隐含调用顺序 |

### 5.2 状态映射

| state | 判定 | next_action |
| --- | --- | --- |
| `not_started` | 没有 guidance run | `start_initialization` |
| `scanning` | 存在 pending batch | `analyze_next_batch` |
| `category_review` | 批次完成但分类目录未确认，或已有分类草稿 | `create_category_draft` 或 `await_user_review` |
| `guide_generation` | 分类已确认，仍有类别缺少方法论或指导 | `create_methodology` 或 `create_guidance` |
| `guide_review` | 存在待确认方法论或指导草稿 | `await_user_review` |
| `incremental` | 当前 snapshot 与 guidance baseline 不一致，或存在 pending change | `inspect_changes` 或 `classify_change` |
| `ready` | 第 9 节所有门禁通过 | `none` |
| `failed` | 当前 run 明确失败 | `restart_initialization` |

当一个状态存在两个候选动作时，以是否已有草稿或 change 记录决定唯一结果。`knowledge_status` 不创建 change、不保存草稿，也不推进 baseline。

状态按以下固定优先级计算，命中后立即停止：没有 run、run 失败、存在待审草稿、存在 pending batch、分类目录未确认、类别正式资产不完整、存在 pending change、baseline 与当前 snapshot 不一致、Ready。待生成类别按 `category_id` 排序；同一类别先生成方法论再生成项目指导。增量变化按 `pendingCategories` 的排序顺序每次处理一个类别。这样即使多个事实同时成立，也只返回一个动作。

## 6. WP-KC-02：首次建库闭环

### 6.1 需求

| ID | 要求 |
| --- | --- |
| KC-INIT-001 | 初始化按现有最大 40 文件批次覆盖当前 CodeGraph snapshot |
| KC-INIT-002 | 批次提交的 `analyzedFiles` 必须与批次文件集合完全一致 |
| KC-INIT-003 | 所有批次完成后才能创建分类目录草稿 |
| KC-INIT-004 | 分类确认后按类别顺序生成方法论和项目指导 |
| KC-INIT-005 | 每个类别必须同时存在正式方法论和正式项目指导 |
| KC-INIT-006 | 快照变化或批次失败时当前 run 失败，重新开始初始化 |

### 6.2 顺序

1. `knowledge_initialization_start` 冻结 snapshot 并创建批次。
2. AI 逐批调用 `knowledge_initialization_next`，按需读取批次中每个文件的源码。
3. AI 提交候选类别、证据和 `analyzedFiles`。
4. PKS 校验 `analyzedFiles` 与批次文件集合相等、证据 hash 属于冻结 snapshot。
5. 全部批次完成后，AI 汇总候选并保存分类目录草稿。
6. 用户确认分类目录。
7. AI 按 `category_id` 排序，每次只生成一个缺失资产：先方法论，后项目指导。
8. 用户逐份确认。最后一个类别的两个资产都确认后，首次建库完成。

批次完成表示 AI 声明已处理批次内全部文件。PKS 不尝试证明模型是否真正理解正文，但不允许通过省略文件伪造覆盖率。

## 7. WP-KC-03：增量更新闭环

### 7.1 需求

| ID | 要求 |
| --- | --- |
| KC-INCR-001 | status 只读发现 baseline 与当前 snapshot 不一致并指示调用 `knowledge_changes` |
| KC-INCR-002 | AI 顺序处理每个受影响类别，不做并行调度 |
| KC-INCR-003 | change payload 保存 `pendingCategories`、`completedCategories` 和每类 level |
| KC-INCR-004 | 一级变化验证成功后自动完成对应类别 |
| KC-INCR-005 | 二级、三级变化必须经过现有草稿确认流程 |
| KC-INCR-006 | 所有受影响类别完成后才推进整个 guidance baseline |
| KC-INCR-007 | `sync` 不再创建无人消费的 semantic update queue |

### 7.2 分级规则

- `fact`：只有现有证据文件被修改，没有新增或删除文件；AI 明确声明指导结论不变；PKS 校验当前 snapshot 和全部证据 hash。通过后只更新来源和版本。
- `guidance`：类别不变，但步骤、不变量、调用流程、配置、测试或发布方式变化。生成该类别项目指导草稿。
- `category`：出现新功能类别、类别合并拆分、职责边界变化，或 AI 无法可靠归入现有类别。生成新的分类目录草稿；确认后补齐受影响类别资产。

`GuidanceChange.payload` 直接保存逐类别进度，不增加新表。每次类别完成后重新计算剩余类别；只有剩余集合为空且没有关联待审草稿时，才写入新 baseline 并标记 change processed。

如果变化没有命中现有类别，AI 仍必须选择 `fact`（确认不影响指导）或 `category`，不能静默推进 baseline。

开发期现有 semantic update queue 不迁移。实现验证时重新初始化 `.project-kb`；Proposal 继续仅用于显式 curated block 和 ADR 修改。

## 8. 审核记录

本期不新增审核表。现有 guidance draft/version 增加或复用以下字段：

- `reviewer`
- `confirmed_at`
- `content_hash`
- `snapshot_id`

拒绝继续使用 `rejection_reason`。正文 hash 或 snapshot 不一致时不能确认。拒绝不会删除旧正式版本，也不会把关联增量变化标记完成。

## 9. WP-KC-04：Ready 与 Finalize 门禁

### 9.1 需求

| ID | 要求 |
| --- | --- |
| KC-GATE-001 | 最新初始化 run 已完成且所有批次均成功 |
| KC-GATE-002 | 每个类别同时存在正式方法论和正式项目指导 |
| KC-GATE-003 | 不存在待审核 guidance draft 或 pending guidance change |
| KC-GATE-004 | 不存在 stale/conflicted knowledge |
| KC-GATE-005 | guidance baseline 等于当前 CodeGraph snapshot |
| KC-GATE-006 | `check/finalize` 返回精确阻塞原因和对应下一动作 |

### 9.2 Ready 定义

只有以下条件全部成立才能返回 `state=ready`：

1. CodeGraph 可用且项目索引内容新鲜；
2. 最新 guidance run 为 `complete`；
3. 批次覆盖数等于总文件数且没有失败文件；
4. 类别数大于零；
5. 每个类别都有当前方法论和当前项目指导；
6. 没有 `incomplete` 或 `awaiting_confirmation` 草稿；
7. 没有未处理 guidance change；
8. guidance baseline snapshot 等于当前 CodeGraph snapshot；
9. stale/conflicted knowledge 均为零。

`finalize --check` 在任一条件不满足时返回非零，并列出具体条件；不再只返回笼统的 `knowledge_review_required`。

## 10. 失败处理

- 批次文件不完整、证据 hash 错误、草稿正文变化：拒绝本次提交，保留当前状态等待 AI 或用户修正。
- CodeGraph snapshot 在初始化或增量处理中变化：当前轮次进入 `failed`，下一动作是从新 snapshot 重新开始；不做 rebase 或恢复。
- AI 客户端或终端中断：不提供专门恢复协议。用户可继续现有可见状态，也可重新初始化知识库；本期不对中断恢复做验收承诺。
- 单客户端假设下不处理并发冲突；现有进程锁保持原状。

## 11. 测试与验收

每个工作包先补测试，再实现行为。

### WP-KC-01

- 无 run、pending batch、待审分类、缺失资产、待审指导、snapshot 变化、Ready 和 failed 的状态映射正例。
- 同一状态不能返回多个动作；status 调用后数据库和文件不变。

### WP-KC-02

- 两个批次、两个类别从空库到 Ready。
- 缺少一个 analyzed file、错误 snapshot、失败批次、缺少方法论、缺少指导均不能完成。

### WP-KC-03

- 单类别一级事实更新自动完成。
- 两个类别变化只完成第一个时 baseline 不前移。
- 二级指导和三级分类没有用户确认时保持 pending。
- 删除文件不能走一级更新；无类别命中不能静默完成。
- sync 后不再新增 semantic queue。

### WP-KC-04

- 每个 Ready 条件都有独立负例。
- 当前仓库临时副本和 gardenserver 只读临时镜像完成首次建库及一次增量闭环。
- 全量测试、相关评测、版本、Changelog、README、审计和知识同步全部通过后，才能把需求标记为完成。

## 12. 实施顺序与交付规则

实施顺序固定为 WP-KC-01、WP-KC-02、WP-KC-03、WP-KC-04。每个工作包独立交付和验收：测试先行，完成实现和文档后运行一次补丁版本递增，再同步 generated knowledge，并报告需要人工复核的 curated knowledge。

本设计文档获用户确认前不开始 WP-KC-01 代码实现。任何需求若需要旧数据迁移、多客户端并发、后台任务或中断恢复，必须作为新工作包重新设计，不能扩入本期。
