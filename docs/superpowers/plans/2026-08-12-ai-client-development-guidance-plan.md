# MCP AI 客户端驱动的通用开发指导实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 在下一功能版本实现由 MCP AI 客户端驱动的通用开发指导知识库：首次分批发现类别、两阶段 Markdown 审核、确认后进入 KnowledgeStore，后续按 CodeGraph 变化进行三级增量更新。

**Architecture:** CodeGraph 继续作为唯一代码事实来源。新增聚焦的工作流模型、存储仓库、初始化服务、草稿服务和增量服务；MCP 只负责公开工具 Schema 与路由，不调用大模型。KnowledgeStore 保存正式状态和历史，目标项目 .project-kb 根目录只保存当前分类/指导草稿与正式阅读版。

**Tech Stack:** Python 3.11+、标准库 sqlite3/dataclasses/hashlib、unittest、CodeGraph 1.5 公共 CLI、stdio MCP、Markdown。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| src/project_knowledge/guidance_models.py | 初始化、批次、类别、指导、草稿、变化集的类型与状态 |
| src/project_knowledge/guidance_store.py | 工作流表的全部读写和事务边界 |
| src/project_knowledge/initialization.py | CodeGraph 快照、稳定分批、覆盖率与断点继续 |
| src/project_knowledge/guidance_workflow.py | 草稿校验、Markdown、正文哈希、确认与入库 |
| src/project_knowledge/incremental.py | 快照差异、影响事实包、三级更新和基线推进 |
| src/project_knowledge/mcp.py | MCP 工具声明、输入 Schema 和服务路由 |
| src/project_knowledge/store.py | SQLite Schema v2 与向前迁移 |
| src/project_knowledge/codegraph.py | 文件清单、源码和影响事实的规范化接口 |
| src/project_knowledge/retrieval.py | 正式指导、新鲜度和待审核链接查询 |
| tests/test_guidance_store.py | 存储、迁移、事务与版本测试 |
| tests/test_initialization_workflow.py | 分批、覆盖率、恢复和快照变化测试 |
| tests/test_guidance_workflow.py | 两阶段审核、哈希确认和文件位置测试 |
| tests/test_guidance_mcp.py | 七个新增 MCP 工具的 Schema、路由和错误测试 |
| tests/test_guidance_incremental.py | 变化去重、三级更新和失败回退测试 |
| tests/test_guidance_e2e.py | 与项目名无关的通用闭环测试 |
| scripts/validate_gardenserver_guidance_workflow.py | gardenserver 只读真实验收 |
| docs/guidance-workflow-guide.md | 中文初始化、审核、查询和增量说明 |

## 需求覆盖矩阵

| 需求 | 实施任务 | 核心证据 |
| --- | --- | --- |
| NV-MODEL-001 | Task 1、Task 4 | Schema v2、正式版本、KnowledgeRecord 查询 |
| NV-INIT-001 | Task 2、Task 7 | 全项目稳定分批、断点恢复、真实覆盖率 |
| NV-MCP-001 | Task 3、Task 4、Task 5 | 两阶段审核、哈希确认、12 个 MCP 工具 |
| NV-INCR-001 | Task 6、Task 7 | 变化范围、三级更新、失败不推进基线 |
| NV-VERIFY-001 | Task 7 | 双项目通用测试、gardenserver 真实审核 |

## Task 1：通用模型与 KnowledgeStore v2

**需求：** NV-MODEL-001

**Files:**

- Create: src/project_knowledge/guidance_models.py
- Create: src/project_knowledge/guidance_store.py
- Create: tests/test_guidance_store.py
- Modify: src/project_knowledge/store.py
- Modify: src/project_knowledge/models.py
- Modify: src/project_knowledge/schemas.py

- [ ] **Step 1: 编写失败的迁移测试**

创建 v1 数据库并写入现有 knowledge 记录。调用 KnowledgeStore.initialize 后断言 schema_version 为 2、旧记录仍存在，并存在以下六张表：

    guidance_runs
    guidance_batches
    guidance_categories
    guidance_drafts
    guidance_versions
    guidance_changes

再调用一次 initialize，断言不丢数据、不重复插入。

- [ ] **Step 2: 运行测试确认 RED**

Run:

    PYTHONPATH=src python3 -m unittest tests.test_guidance_store -v

Expected: FAIL，schema_version 仍为 1 或工作流表不存在。

- [ ] **Step 3: 定义最小类型**

在 guidance_models.py 定义以下状态，名字后续任务不得改变：

    RunStatus = Literal[
        "scanning", "category_review", "categories_confirmed",
        "guidance_generation", "guidance_review", "complete", "failed",
    ]
    BatchStatus = Literal["pending", "completed", "failed"]
    DraftKind = Literal["category_catalog", "guidance"]
    DraftStatus = Literal[
        "incomplete", "awaiting_confirmation", "confirmed", "rejected"
    ]
    UpdateLevel = Literal["fact", "guidance", "category"]

定义 GuidanceRun、GuidanceBatch、GuidanceCategory、GuidanceDraft、GuidanceVersion、GuidanceChange dataclass。所有结构均提供 to_dict/from_dict；所有 ID 为稳定字符串，时间为 ISO-8601。

- [ ] **Step 4: 实现 Schema v2 和 GuidanceStore**

KnowledgeStore.initialize 使用 CREATE TABLE IF NOT EXISTS 建六张表，把 SCHEMA_VERSION 改为 2。GuidanceStore 只接收已打开的 KnowledgeStore，提供：

    create_run / get_run
    save_batch / next_pending_batch / list_batches
    save_category / list_categories
    save_draft / get_draft / list_pending_drafts
    save_version / current_version / list_versions
    save_change / pending_changes / mark_change_processed

跨表状态改变必须包在 KnowledgeStore.transaction 中。

- [ ] **Step 5: 补正负测试并验证 GREEN**

覆盖非法状态、重复 batch_id 幂等更新、类别改名不改变 category_id、每类仅一个 current version、事务异常回滚。

Run:

    PYTHONPATH=src python3 -m unittest tests.test_guidance_store tests.test_schemas -v

Expected: PASS。

- [ ] **Step 6: Commit**

    git add src/project_knowledge/store.py src/project_knowledge/models.py
    git add src/project_knowledge/schemas.py src/project_knowledge/guidance_models.py
    git add src/project_knowledge/guidance_store.py tests/test_guidance_store.py
    git commit -m "feat: 建立开发指导工作流存储模型"

## Task 2：CodeGraph 快照、稳定分批与断点继续

**需求：** NV-INIT-001

**Files:**

- Create: src/project_knowledge/initialization.py
- Create: tests/test_initialization_workflow.py
- Modify: src/project_knowledge/codegraph.py
- Modify: src/project_knowledge/guidance_store.py

- [ ] **Step 1: 编写首次初始化失败测试**

Fake CodeGraph 返回 path、language、contentHash、module。断言 start 返回 total_files、covered_files=0、status=scanning；批次按模块和路径稳定排序。同一快照重复 start 返回原 run_id。

- [ ] **Step 2: 运行测试确认 RED**

    PYTHONPATH=src python3 -m unittest tests.test_initialization_workflow -v

Expected: FAIL，InitializationWorkflow 不存在。

- [ ] **Step 3: 实现 CodeGraphClient.snapshot**

规范化路径和语言，优先使用 contentHash。公共输出缺少 hash 时只计算文件清单中对应本地文件的 SHA-256。对排序后的 path/language/hash JSON 计算 snapshot_id。禁止读取 CodeGraph 私有数据库。

- [ ] **Step 4: 实现确定性分批**

规则固定为：

1. 排除 .project-kb、.codegraph 和配置 exclude；
2. 先按 CodeGraph module 或第一层业务目录分组；
3. 单批最多 40 文件；
4. 组内按路径排序，超限连续切片；
5. batch_id 由 run_id、ordinal 和 files 的 SHA-256 前 16 位组成。

next_batch 返回批次、文件事实、符号摘要和按需源码提示，不调用大模型。

- [ ] **Step 5: 实现提交与恢复**

submit_batch 校验 run、batch、snapshot、候选类别必要字段、证据和置信度。相同内容重复提交幂等；不同内容覆盖结果。快照变化时只把包含新增、修改或删除文件的批次标回 pending。

- [ ] **Step 6: 验证覆盖率**

覆盖空项目、跨模块、41 文件切片、中断恢复、快照变化、失败批次。只有全部 completed 才返回 ready_for_category_draft=true；失败文件进入 uncovered_files。

Run:

    PYTHONPATH=src python3 -m unittest tests.test_codegraph tests.test_initialization_workflow -v

Expected: PASS。

- [ ] **Step 7: Commit**

    git add src/project_knowledge/codegraph.py src/project_knowledge/initialization.py
    git add src/project_knowledge/guidance_store.py tests/test_initialization_workflow.py
    git commit -m "feat: 实现CodeGraph分批初始化工作流"

## Task 3：分类目录草稿与第一阶段审核

**需求：** NV-MCP-001 分类阶段

**Files:**

- Create: src/project_knowledge/guidance_workflow.py
- Create: tests/test_guidance_workflow.py
- Modify: src/project_knowledge/schemas.py
- Modify: src/project_knowledge/guidance_store.py

- [ ] **Step 1: 编写可见分类草稿失败测试**

提交 category_catalog，至少包含 category_id、name、purpose、applies_to、excludes、samples、evidence、confidence、unknowns。断言生成绝对路径：

    <root>/.project-kb/功能分类目录-待审核.md

返回 draft_id、content_hash、awaiting_confirmation。

- [ ] **Step 2: 运行测试确认 RED**

    PYTHONPATH=src python3 -m unittest tests.test_guidance_workflow.GuidanceWorkflowTests.test_category_catalog_requires_visible_markdown -v

Expected: FAIL，GuidanceWorkflow 不存在。

- [ ] **Step 3: 实现严格 Schema 和语义校验**

逐条确认 evidence path 属于本轮快照、hash 一致、sample 属于覆盖文件；confidence 为 0 到 1。覆盖率不足或存在失败批次时允许保存，但状态必须为 incomplete 且不能确认。

- [ ] **Step 4: 渲染中文分类目录**

固定包含草稿状态、ID、正文哈希、快照、覆盖率、类别用途、适用和不适用范围、样本、证据、类别关系、合并拆分建议、置信度和待确认事项。使用 atomic_write 直接写 .project-kb 根目录，不创建子目录。

- [ ] **Step 5: 实现哈希确认**

正文哈希排除机器状态区。confirm_draft 重新读取磁盘，校验 draft_id、参数哈希、存储哈希、磁盘哈希、覆盖率和当前快照。成功后保存类别、生成 功能分类目录.md、删除待审核文件，把 run 转成 categories_confirmed。

- [ ] **Step 6: 补负向测试**

覆盖用户修改正文、机器状态变化、不完整草稿、证据 hash 错误、路径穿越、失败不改正式类别、重复确认幂等。

Run:

    PYTHONPATH=src python3 -m unittest tests.test_guidance_workflow -v

Expected: PASS。

- [ ] **Step 7: Commit**

    git add src/project_knowledge/guidance_workflow.py
    git add src/project_knowledge/guidance_store.py src/project_knowledge/schemas.py
    git add tests/test_guidance_workflow.py
    git commit -m "feat: 实现功能分类目录审核"

## Task 4：两层指导审核、正式入库与查询

**需求：** NV-MODEL-001、NV-MCP-001

**Files:**

- Modify: src/project_knowledge/guidance_workflow.py
- Modify: src/project_knowledge/guidance_store.py
- Modify: src/project_knowledge/retrieval.py
- Modify: src/project_knowledge/models.py
- Modify: src/project_knowledge/schemas.py
- Modify: tests/test_guidance_workflow.py
- Create: tests/test_guidance_retrieval.py

- [ ] **Step 1: 编写两层指导失败测试**

指导必须含基本信息、methodology、project_adaptation、variants、evidence、unknowns。确认前文件为：

    .project-kb/<类别名称>-开发指导-待审核.md

确认后正式文件存在，草稿删除，KnowledgeStore 返回正式版本。

- [ ] **Step 2: 运行测试确认 RED**

    PYTHONPATH=src python3 -m unittest tests.test_guidance_workflow.GuidanceWorkflowTests.test_confirmed_guide_is_exact_reviewed_body -v

Expected: FAIL，尚不支持指导草稿或正式版本。

- [ ] **Step 3: 实现质量门**

methodology 必须含 analysis、steps、invariants、testing、pitfalls。project_adaptation 必须含 entrypoints、locations、call_flow、registration、data_and_config、steps、invariants、testing、release、rollback。只有 variants 和 unknowns 可为空。只列文件但没有步骤、不变量、测试或回滚时状态为 incomplete。

- [ ] **Step 4: 实现正式版本事务**

确认时同一事务：

1. 保存 immutable guidance_versions；
2. 上一版本 is_current=0；
3. upsert ID 为 guide.<category_id> 的 development-guide KnowledgeRecord；
4. ownership=curated、confidence=verified；
5. 保存来源、快照和版本关系。

事务成功后才渲染正式 Markdown 并删除草稿。文件失败不得留下半完成数据库状态。

- [ ] **Step 5: 扩展查询**

search/get/context 优先返回正式 development-guide。存在待审核修订时附加 freshness=potentially_stale、draft_id 和绝对草稿路径，但不得混入草稿正文。

- [ ] **Step 6: 补逐份确认与查询测试**

覆盖两个类别只确认一个、正文完全一致、历史版本、待审核提示、拒绝、重复确认、类别检索、context 优先返回两层指导。

Run:

    PYTHONPATH=src python3 -m unittest       tests.test_guidance_workflow tests.test_guidance_retrieval tests.test_retrieval_wp06 -v

Expected: PASS。

- [ ] **Step 7: Commit**

    git add src/project_knowledge/guidance_workflow.py
    git add src/project_knowledge/guidance_store.py src/project_knowledge/retrieval.py
    git add src/project_knowledge/models.py src/project_knowledge/schemas.py
    git add tests/test_guidance_workflow.py tests/test_guidance_retrieval.py
    git commit -m "feat: 实现两层开发指导审核入库"

## Task 5：公开七个 MCP 工作流工具

**需求：** NV-MCP-001

**Files:**

- Modify: src/project_knowledge/mcp.py
- Create: tests/test_guidance_mcp.py
- Modify: tests/test_integration.py

- [ ] **Step 1: 编写工具清单失败测试**

断言工具总数从 5 变为 12，新增名称：

    knowledge_initialization_start
    knowledge_initialization_next
    knowledge_initialization_submit
    knowledge_draft_save
    knowledge_draft_confirm
    knowledge_changes
    knowledge_update_submit

写工具 readOnlyHint=false、destructiveHint=false。

- [ ] **Step 2: 运行测试确认 RED**

    PYTHONPATH=src python3 -m unittest       tests.test_guidance_mcp.GuidanceMCPTests.test_lists_seven_workflow_tools -v

Expected: FAIL，实际仍为 5。

- [ ] **Step 3: 添加完整 inputSchema**

逐工具声明 required、enum、数组和长度。禁止客户端指定数据库或输出路径；projectPath 只选择项目，Markdown 路径由服务确定。

knowledge_draft_save 使用 action 枚举 save/reject。action=save 时必须提交 kind、runId 和结构化 content；action=reject 时必须提交 draftId、reviewer、reviewReason。拒绝操作把草稿状态改为 rejected、保留审核记录并维持当前正式版本，不新增第八个 MCP 工具。

- [ ] **Step 4: 显式路由**

MCPServer._call 显式创建 InitializationWorkflow、GuidanceWorkflow、IncrementalWorkflow 并逐名称调用，不使用动态方法名。确认接口准确传 draftId、contentHash、reviewer。

- [ ] **Step 5: 统一结果**

成功结果包含 status 和 next_actions；产生草稿时还含绝对 path、draft_id、content_hash。失败包装 isError=true，正式数据库与文件不变。

- [ ] **Step 6: 补 MCP 正负测试**

覆盖缺字段、错误 enum、未知批次、过期 hash、跨项目 projectPath、失败后继续响应。把旧集成测试工具数更新为 12。

Run:

    PYTHONPATH=src python3 -m unittest tests.test_guidance_mcp tests.test_integration -v

Expected: PASS。

- [ ] **Step 7: Commit**

    git add src/project_knowledge/mcp.py
    git add tests/test_guidance_mcp.py tests/test_integration.py
    git commit -m "feat: 公开开发指导MCP工作流"

## Task 6：CodeGraph 变化集与三级增量

**需求：** NV-INCR-001

**Files:**

- Create: src/project_knowledge/incremental.py
- Create: tests/test_guidance_incremental.py
- Modify: src/project_knowledge/codegraph.py
- Modify: src/project_knowledge/guidance_store.py
- Modify: src/project_knowledge/guidance_workflow.py
- Modify: src/project_knowledge/retrieval.py

- [ ] **Step 1: 编写只分析变化范围的失败测试**

确认指导和快照后让 Fake CodeGraph 返回一个 hash 变化。knowledge_changes 只返回变化文件、影响范围和相关指导。记录 source 调用，断言不读取未变化源码。

- [ ] **Step 2: 运行测试确认 RED**

    PYTHONPATH=src python3 -m unittest tests.test_guidance_incremental -v

Expected: FAIL，IncrementalWorkflow 不存在。

- [ ] **Step 3: 实现差异与事实包**

比较最后已处理 snapshot 和当前 snapshot，得到新增、修改、删除。新增/修改读取符号和必要源码；删除保留旧事实。CodeGraph impact 最大深度 2、每个变化最多 50 关联项。change_id 由基线、目标快照、排序变化文件的 SHA-256 生成。

- [ ] **Step 4: 一级事实更新**

level=fact 要求 AI 声明指导结论未变并提交新 evidence。复核路径和 hash 后创建正文不变的新版本，只更新证据、快照和记录，再推进已覆盖变化。

- [ ] **Step 5: 二级指导修订**

level=guidance 必须指定现有 category_id 和完整两层正文。保存待审核指导，保留正式版并标 potentially_stale；用户确认后推进所覆盖变化。

- [ ] **Step 6: 三级分类调整**

level=category 生成 功能分类目录-待审核.md，描述新增、删除、合并、拆分、改名或边界变化。分类确认前禁止确认相关指导；确认后为受影响类别进入 guidance_generation。

- [ ] **Step 7: 失败回退与去重**

覆盖 CodeGraph 失败、影响失败、无效证据、部分提交、相同内容、用户拒绝。失败不推进快照；相同 change hash + content hash 不重复建草稿。

Run:

    PYTHONPATH=src python3 -m unittest       tests.test_guidance_incremental tests.test_guidance_retrieval -v

Expected: PASS。

- [ ] **Step 8: Commit**

    git add src/project_knowledge/incremental.py src/project_knowledge/codegraph.py
    git add src/project_knowledge/guidance_store.py
    git add src/project_knowledge/guidance_workflow.py src/project_knowledge/retrieval.py
    git add tests/test_guidance_incremental.py
    git commit -m "feat: 实现开发指导三级增量更新"

## Task 7：通用端到端、gardenserver 验收、文档与版本

**需求：** NV-VERIFY-001

**Files:**

- Create: tests/test_guidance_e2e.py
- Create: scripts/validate_gardenserver_guidance_workflow.py
- Create: docs/guidance-workflow-guide.md
- Modify: README.md
- Modify: docs/next-version-plan.md
- Modify: docs/project-knowledge-system-audit.md
- Modify: docs/project-knowledge-system-design.md
- Modify: tests/test_documentation_roadmap.py
- Modify: src/project_knowledge/__init__.py
- Modify: plugins/project-knowledge/.codex-plugin/plugin.json
- Modify: CHANGELOG.md

- [ ] **Step 1: 通用端到端测试**

创建两个不同名称的临时项目，使用同一 Fake CodeGraph 和 MCP 调用序列：初始化、逐批提交、分类审核、逐类指导、入库查询、增删改、三级更新。断言两个项目行为一致且实现没有按项目名分支。

- [ ] **Step 2: 运行端到端测试**

    PYTHONPATH=src python3 -m unittest tests.test_guidance_e2e -v

Expected: PASS。

- [ ] **Step 3: gardenserver 只读验收脚本**

脚本读取 GARDENSERVER_ROOT，验证 CodeGraph status、批次数、覆盖率和事实包。不得修改业务源码，不预置“登录、花园、工会”。第一次运行必须生成并返回 .project-kb/功能分类目录-待审核.md 的绝对路径，由用户真实审核。

- [ ] **Step 4: 验证真实增量不全扫源码**

在可恢复测试分支或独立 fixture 修改一个 Lua 文件，记录 snapshot 差异和 source 调用，确认只读取变化文件和深度 2 的影响上下文。恢复变化后确认没有遗留待处理状态。

- [ ] **Step 5: 更新中文文档**

guidance-workflow-guide 说明 12 个工具、初始化顺序、可点击草稿、哈希确认、三级更新、失败恢复和旧 watch 边界。README 在功能真实完成后才移除“尚未实现”。审计和计划只按测试证据标完成。

- [ ] **Step 6: 完整验证**

    PYTHONPATH=src python3 -m unittest discover -s tests -q
    PYTHONPATH=src python3 -m project_knowledge --version
    PYTHONPATH=src python3 -m project_knowledge status . --json
    PYTHONPATH=src python3 -m project_knowledge check . --json
    GARDENSERVER_ROOT=/mnt/d/Github-Poj/gardenserver       PYTHONPATH=src python3 scripts/validate_gardenserver_guidance_workflow.py

Expected: 测试全通过；gardenserver CodeGraph 可用；草稿可打开；业务源码无修改；正式查询与待审核提示正确；pending_files=0；curated/ADR 状态明确。

- [ ] **Step 7: 版本和知识同步**

全部功能完成后只运行一次：

    python3 scripts/bump_version.py "实现MCP AI客户端驱动的通用开发指导闭环"
    PYTHONPATH=src python3 -m project_knowledge sync . --json

禁止复用已经被计划文档提交占用的版本号；功能交付版本按实施时唯一版本源递增。

- [ ] **Step 8: 最终提交**

    git add src tests scripts docs README.md CHANGELOG.md
    git add plugins/project-knowledge/.codex-plugin/plugin.json .project-kb
    git status --short
    git commit -m "feat: 完成通用开发指导知识库闭环"

提交前确认用户原有 LICENSE 修改不在暂存区。
