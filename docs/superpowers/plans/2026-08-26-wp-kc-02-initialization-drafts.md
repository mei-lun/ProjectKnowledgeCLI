# WP-KC-02 首次建库草稿闭环实施计划

需求基线：`docs/superpowers/specs/2026-08-26-single-client-knowledge-closed-loop-design.md`

范围：KC-INIT-001～008。单 AI 客户端、单本地知识库、开发阶段；不实现旧数据迁移、多客户端并发、后台任务或终端恢复。

“自动生成”表示 AI 客户端按 `knowledge_status.guidance_workflow.next_action` 连续调用现有 MCP 工具，直到所有草稿保存完成；过程中不要求用户确认。PKS 负责确定性状态、校验、保存和检索，不新增模型 Provider。

## 执行顺序

1. 补齐 WP-KC-01 收口回归
   - 当前 CodeGraph snapshot 与 guidance baseline 比较。
   - 已确认正式版本计入已生成资产。
   - 中文任务可命中仅存在于 `guidance_drafts` 的待审草稿。
   - 当前版本、README、审计、CodeGraph 状态和活动评测报告一致。

2. 批次覆盖契约（KC-INIT-001、KC-INIT-002、KC-INIT-006）
   - MCP `knowledge_initialization_submit` 增加必填 `analyzedFiles`。
   - `analyzedFiles` 与批次文件集合必须完全相等；缺少、多余、重复都拒绝。
   - snapshot 变化或显式批次错误将 run 标记为 `failed`，下一动作固定为 `restart_initialization`。
   - 测试文件：`tests/test_initialization_workflow.py`、`tests/test_guidance_mcp.py`。

3. 分类草稿进入无确认生成链（KC-INIT-003）
   - 所有批次完成后，状态返回 `create_category_draft` 和聚合候选摘要。
   - AI 保存分类目录草稿时，同时保存该 run 的临时类别记录，供后续草稿生成定位；不创建正式知识版本。
   - 分类草稿保持 `awaiting_confirmation`，但 run 进入 `guidance_generation`。

4. 方法论与项目指导顺序生成（KC-INIT-004、KC-INIT-005）
   - 类别按 `category_id` 排序。
   - 每个类别先缺失的 `methodology`，再缺失的 `guidance`。
   - `knowledge_status` 返回唯一 `next_action` 和 `next_draft` 目标。
   - 每个草稿保存 snapshot，并从类别/草稿证据生成来源引用；无有效来源不允许完成生成链。

5. 草稿完成即基础 Ready（KC-INIT-007）
   - 所有类别的分类、方法论和指导草稿完整后，run 标记为 `complete`。
   - 写入当前 snapshot 作为 guidance baseline。
   - `awaiting_confirmation` 不阻塞 `state=ready`；`incomplete` 仍阻塞。

6. 显式草稿检索与按需审核（KC-INIT-008）
   - 保存草稿时写入 `KnowledgeStore`，`ownership=draft`、非 verified confidence、带来源和 snapshot 标签。
   - `knowledge_search`/`knowledge_get` 可显式查看草稿。
   - 默认 `knowledge_context` 命中草稿时只返回 `review_required/review_drafts`，不注入草稿正文。
   - 确认草稿后同一资产升级为 curated；拒绝草稿后不再作为可用草稿检索。

7. 端到端验收
   - 从空库创建至少两个批次、两个类别。
   - 不确认任何语义草稿，依次保存分类、方法论和指导草稿后达到 `ready`。
   - 负例覆盖 analyzedFiles 不完整、错误 snapshot、失败批次、缺草稿、无来源。
   - 任务命中返回审核门；主动审核可从 status 列出全部待审草稿。

8. 交付门
   - 运行相关 unittest、完整 unittest 和文档一致性测试。
   - 更新 README、审计状态和验收证据。
   - 本批只运行一次 `python scripts/bump_version.py "实现首次建库草稿闭环"`。
   - 运行版本检查、知识同步、状态/上下文/影响检查和代码审查。

## 停止条件

- 本轮只完成 WP-KC-02，不提前实现 WP-KC-03 增量分类或 WP-KC-04 finalize 总门禁。
- 若现有数据模型无法在不迁移的前提下表达临时类别，则直接调整开发期 schema，不编写迁移代码。
- 任何步骤失败时保持当前状态可见，不以占位字段或空草稿宣称完成。
