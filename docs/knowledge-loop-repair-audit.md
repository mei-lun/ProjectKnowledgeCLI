# 知识闭环现状审计与修复方案

审计日期：2026-09-14。基线提交：59265e3。工作包：WP-KC-06（承接 WP-KC-05、WP-KC-03、WP-NPM-01）。

用户已确认：开发实现和验证完成后，由 AI 客户端自动生成待审核草稿；不再等待用户额外说“任务完成”。发布正式知识仍须用户明确审核。本文件先于代码修改建立，作为本批实施和验收依据。

## 1. 当前现状与证据

| 需求 ID | 已复现问题 | 来源与影响 |
| --- | --- | --- |
| KC6-001 | 默认 AGENTS 只有查询和报告要求，缺少初始化、同步和收口动作 | service.py:AGENTS_BODY；插件 Skill 不由默认安装部署。不能以安装索引代替 AI 生成指导 |
| KC6-002 | hook 登记后，同 taskId 的 complete 直接返回未确认记录，丢失本次文件和测试 | task_workflow.py:complete；register_pending → complete 后 user_confirmed=false，changed_files=[] |
| KC6-003 | complete 没有同步和影响分析，没有返回 runId/changeId；保存到旧 run 被快照校验拒绝 | task_workflow.py、incremental.py、guidance_workflow.py |
| KC6-004 | 保存和审核草稿后任务仍 pending；没有 generated/failed 完整转换和可靠草稿关联 | task_workflow.py:_result 仅以正文中的 _task_id 查找草稿 |
| KC6-005 | 两类别不能连续生成；保存第一份未审草稿即可推进 baseline，changes 返回 current 而数据库仍 pending | incremental.py:submit_update、guidance_workflow.py:save_draft/_complete_linked_change |
| KC6-006 | 新文件没有匹配旧分类时返回 pending+none；分类接口又拒绝空 affected_categories | incremental.py:submit_update；新增、删除和分类覆盖不能靠调用方猜测 |
| KC6-007 | 验证器错误读取 result.tools.tools；错误 ID、缺握手也可被判成功；失败不影响安装结果 | codex.py:verify_project_mcp、service.py:_install_codex_integration |
| KC6-008 | Schema=4 的既有数据库缺 task_completions，status/context 报错 | retrieval.py:_ensure_current_schema；纳入本批必要前置修复，保留已有知识 |

复现时相关 30 项单元测试通过，但未覆盖上述组合场景。真实 CodeGraph 1.5 文件输出含 path/language/nodeCount/size，不含 symbols；不得通过只在 mock 中提供 symbols 冒充真实影响分析。当前项目没有 Codex MCP 注册，现有工具会话也不提供 knowledge_*；本批修复项目级接入与诊断，不恢复已废弃的全局 Codex 安装路径。

## 2. 架构与边界

PKS 管理事实快照、步骤、持久化进度、校验和审核。Codex 使用当前任务上下文生成正文。不新增模型 Provider、后台 AI daemon 或自动发布正式知识。Git hook 只同步/登记，不生成语义正文；没有实际接入客户端的 hook 不宣称默认可用。

默认流程：安装 → status → 必要时全文件分批初始化和分类/方法论/指导草稿 → context/impact → 实现及验证 → task_complete → 客户端根据返回的工具和参数逐项提交 → 所有草稿完整后停止生成 → 用户审核 → 正式知识与已处理基线更新 → 下次查询。

开发完成（developmentCompleted）与用户审核（userConfirmed/知识草稿确认）分离。兼容旧 userConfirmed=true 收口调用；新客户端不伪造用户确认，传 developmentCompleted=true。只有用户确认草稿正文 hash 的操作才发布正式知识。

## 3. 状态和接口约定

### 3.1 任务记录

- taskId 是稳定幂等键。hook 登记仅为 observed；真正收口补齐摘要、文件、符号、验证结果和当前快照。
- 已收口的相同请求返回相同任务和当前动作；不同内容不得静默覆盖，需显式 retry。hook 不覆盖已收口记录。
- pending 表示还有需要生成的项；generated 表示所有所需产物完整落盘（可待审核）；skipped 必须有原因；failed 必须有原因和重试入口。无变化明确 no_update，不永久 pending。
- 持久化任务与 change 的关联、开发完成标志和生成进度。全部 API 返回唯一 next_action；需要调用时同时返回 next_tool 与可直接补正文的 arguments，及 evidence/current guidance/remaining items。
- 收口先同步，后取快照、分析变化并绑定当前 change/run。同步失败或生成期间快照变化不发布旧事实。重复收口不能在错误快照下继续生成。

### 3.2 生成与审核分离

- IncrementalWorkflow 是变化事实和基线的唯一负责人；TaskCompletionWorkflow 复用它，不复制第二套变化推断。
- 每类别分别维护待生成、草稿已就绪、待审核、已处理。生成次序稳定；第一个类别待审不会阻止第二个类别生成。
- 草稿关联使用显式 taskId/changeId；内部保留字段由服务端写入并校验，不信任客户端自行填写。
- 所有 required 产物完整且证据有效时任务 generated；未审正文不进入正式检索。
- 保存草稿不能推进增量已处理 baseline。审核一个类别也不能提前推进多类别 baseline；仅全部项处理完成后推进。
- 拒绝草稿重新打开该项生成；快照漂移使本轮失败，保留原因并允许显式重试。草稿路径按 run 隔离，防止后续任务覆盖待审文件。
- 初始分批扫描完成基线与增量审核基线的规则保持区分。增量 run 不能被当作“新的全项目初始化”。

### 3.3 变化覆盖

- 同步后依据真实当前/已处理快照识别新增、修改和删除，调用方文件列表用于任务证据和校验，不作为唯一变化源。
- 影响范围使用 CodeGraph 公共接口和已有指导证据；文件列表无 symbols 时使用公开查询得到实际符号。结果有上限，截断和无法判定必须公开。
- 新增/未归类变化返回分类生成入口；不能因为 affected_categories 为空直接返回 none。
- 删除保留 old_hash/删除事实，不要求读取不存在的源文件；无足够存活证据时返回明确人工判断项，不能伪造证据。
- 允许有证据的无需指导更新决定，记录原因；不把无法分类或分析失败当作无需更新。

### 3.4 安装与验证

- 完整默认指令随 Python 包安装，插件读取/复用同一流程，避免维护两套相互矛盾的要求。
- init/install 保留用户配置和自有块边界；验证使用最终配置的 command/args/cwd/env。
- 验证在写锁释放后进行；完整 initialize/initialized/tools/list/tools/call 顺序、响应 ID、错误、必要工具与查询结果都需校验。
- 服务能启动、工具能列出、查询能成功、Codex 新会话能实际调用分别报告，不能互相冒充。
- 超时、启动器缺失、查询错误返回非零和结构化分阶段原因，保留已写配置供诊断，禁止谎报安装成功。
- 旧数据库增量升级，Schema 递增并覆盖旧 v4 缺表样本；只读诊断不隐式写数据库。

## 4. 执行计划

> For agentic workers: use subagent-driven-development or executing-plans task by task. 本文件同时作为现状审计、设计和本批执行计划；用户已授权按文档执行。

Goal: 修复 KC6-001～008，使默认客户端能够完成开发后待审知识生成，且不提前发布知识。

Architecture: 复用 TaskCompletionWorkflow、IncrementalWorkflow、GuidanceWorkflow；安装验证独立于写锁。Python 标准库/SQLite、现有 CodeGraph CLI、Node 启动器。

- [x] A. 先补 MCP 正负协议/安装测试，再修复 codex.py 和 service.py 验证时机、启动契约及失败退出；对齐 npm 验证器。对应 KC6-007。
- [x] B. 先补多类别、新文件、删除、拒绝、快照漂移样本，再修复 incremental.py/guidance_workflow.py 的生成与审核进度、基线、运行/草稿隔离。对应 KC6-003/005/006。
- [x] C. 先补旧 v4 缺表、hook→自动收口、重复、失败重试、草稿→任务状态测试；升级 store.py，修复 task_workflow.py，扩展 MCP 参数和 status/next_action。对应 KC6-002/003/004/008。
- [x] D. 默认安装指令、插件和 hook 使用同一收口契约；修正 guidance_offer 缺知识判定，补安装与无插件默认流程样本。对应 KC6-001。
- [ ] E. 跑完整 Python/Node 测试、真实 CodeGraph/MCP 安装和变化样本；独立代码审查后修复发现。真实 Codex 无法执行时明确保留该验收项，不写完成。
- [ ] F. 更新 README、主审计、CHANGELOG；整批仅运行一次 bump_version.py 中文说明；运行 --version；同步 generated knowledge，报告 curated 待审与未完成验收。

重点测试命令（在隔离工作区设置 PYTHONPATH=src）：

```powershell
rtk proxy python -m unittest tests.test_task_completion tests.test_guidance_incremental tests.test_guidance_mcp tests.test_knowledge_loop_status tests.test_codex_project_scope tests.test_codex_bootstrap -q
rtk proxy python -m unittest discover -s tests -q
rtk proxy node --test npm/test/agent-installer.test.js npm/test/runtime.test.js
rtk proxy python scripts/validate_npm_bootstrap.py --help
rtk proxy python -m project_knowledge --version
rtk proxy python -m project_knowledge sync . --json
```

## 5. 验收标准

| 层级 | 通过条件 | 初始状态 |
| --- | --- | --- |
| 单元与协议 | 缺握手/错误 ID/列表格式/缺表/未完成/重复/失败等负例不误报成功 | 待执行 |
| 两类别增量 | 连续生成两份待审草稿；生成完成，baseline 不动；只审一份仍不动；全部审核后推进 | 待执行 |
| 新增与删除 | 未归类新文件可进入分类；删除事实保留；不存在有变化却 pending+none | 待执行 |
| 真实运行 | npm 安装后的配置启动 MCP，查询成功；真实 CodeGraph 文件格式与图查询可用 | 待执行 |
| 真实客户端 | 默认安装后新 Codex 会话自动检索、开发、生成草稿；明确审核后下一任务检索到新知识 | 待执行 |
| 交付 | 文档、版本、生成知识同步、人工知识待审说明齐全 | 待执行 |

## 6. 执行记录

- 建立独立 worktree 时，现有 Windows post-checkout hook 报 Exec format error；检出成功。主工作区已有的 manifest/project-map 改动保留。本批不会用该 hook 的存在宣称自动收口已运行。
- 初始 status/context 均因 task_completions 缺表失败；knowledge_impact 可调用，返回受影响 curated.architecture 与 curated.feature.guide.generation 为 potentially_stale。后续必须复核和报告。
- 用户追加授权将项目级接入诊断纳入本批，并授权使用当前账号执行真实 Codex 临时项目验收。Codex 0.154.0-alpha.6.2 实测需在项目实际工作目录启动；单独 -C 的配置发现表现不同。未更改用户全局信任。
- 真实客户端暴露两项协议问题：Windows 非 UTF-8 输出使 tools/list 超时；cacheScope=server 不符合当前 Rust MCP SDK 的 public/private 枚举。修复入口 UTF-8 和按协商协议返回工具列表，新增回归测试。
- 真实客户端工具调用日志确认：修复后直接调用 knowledge_status/context、保存方法论/指导；在后续折扣功能开发任务自动调用 knowledge_task_complete、knowledge_update_submit。任务 calculator-discount-20260914 为 generated，development_completed=1，user_confirmed=0；正式版本 0，待审变化 1，baseline 未提前推进。业务 6 项测试通过。验收过程中曾使用 PATH 中旧版 CLI 的 shell sync 失败，当前注册 MCP 使用隔离安装的新版本成功。
- 同步后保留工作流知识的组合测试发现：普通 Markdown 导入会与工作流草稿路径冲突，并删除原正式记录。已按数据库所有权保留工作流记录，跳过其投影文件重导入；这属于 KC6-004 的跨任务可检索验收范围。
- 第一轮全量 436 项测试执行完毕：1 个旧 Schema 断言失败、1 个未初始化 hook-only 用例失败、2 个 1200 Token 检索回归错误，正在逐项修正和复验，不据此标记全部完成。
- 合并前结果：上述问题及第二轮暴露的任务动作断言、工作流 Markdown 新鲜度问题均修复；最后受影响测试集 88 项通过，Node 18 项通过、1 项 Unix 专属跳过，当前补丁版本 npm 打包/隔离安装/重复 init/status/context/卸载保留知识验收通过。
- 独立审查的四项状态问题均修复：初始化排除增量 run；任务绑定初始化并刷新状态；目录修订重新计算 provisional 要求；latestDrafts 显式记录最新提交，支持恢复早先正文。两类别审核、删除、失败重试与恢复等组合用例通过。
- 用户批准临时示例审核后，真实 Codex 发布并通过 knowledge_search/knowledge_get 读到 discount 的 fresh/verified 正式指导。该会话发现重新初始化后遗留旧类别变化；新增“全部正式资产已审核且当前快照一致后取代旧变化”处理及回归后，本地复验 next_action=none、pending_changes=[]、drafts.total=0、pending_files=[]，同步后正式知识仍存在。
- 单独的新会话回读尝试超时，不能将其记为额外一次成功；先前真实审核会话中的正式知识检索已成功。检索全策略质量评测耗时较长，应用户合并指令中止，evaluation/reports/latest.json 保留历史结果，未修改历史 package_version 冒充本版本评测。E/F 的最终评测验收仍保留待完成，不宣称全部生产质量门通过。
- 用户要求将本 worktree 代码合并到 main。全部源码、测试、说明和版本改动一并合入；运行时配置和生成知识在 main 重新生成，保留主工作区既有修改的备份。整批补丁版本仅递增一次。
