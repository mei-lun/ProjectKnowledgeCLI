# 项目级知识库最小需求审计与实施基线

> 当前版本：0.1.48
> 复核日期：2026-08-24  
> 报告状态：Phase 0～2 确定性检索基线已交付；发布证据正在重新对齐，最终生产质量门尚未通过
> 默认语言：中文

## npm 一键安装交付复核：WP-NPM-01

本工作包对应 `docs/superpowers/specs/2026-08-24-npm-bootstrap-design.md` 和 `docs/superpowers/plans/2026-08-24-npm-bootstrap.md`。Windows 首发实现、真实安装样本、0.1.48 版本递增和生成知识同步均已完成；同步后 `stale_knowledge=0`、`conflicted_knowledge=0`。

| 需求 ID | 当前结论 | 验收证据与边界 |
| --- | --- | --- |
| NPM-001 | 已完成 | npm 包提供 `project-kb` Node 启动器，并透明转发 Python CLI 参数、stdio、退出码和信号 |
| NPM-002 | 已完成 | 启动器发现 Python 3.11+，按版本创建原子托管 venv，失败清理临时目录并使用完成标记和进程锁 |
| NPM-003 | 已完成 | npm 包内置同版本 Python wheel，并固定依赖 `@colbymchenry/codegraph@1.5.0`；`CODEGRAPH_COMMAND` 使用 npm 所有的绝对路径 |
| NPM-004 | 已完成 | 成功 `init` 后自动写入 `AGENTS.md` 和 `.codex/config.toml` 自有块；CodeGraph 初始化失败时不写 Codex 集成 |
| NPM-005 | 已完成 | 完整 TOML 在写入前后校验；用户自有 `mcp_servers.project_knowledge` 冲突明确失败；重复初始化幂等，卸载保留用户配置和知识数据 |
| NPM-006 | 已完成 | `scripts/build_npm_package.py` 从 Python 唯一版本源生成 `project-kb-cli` manifest，拒绝错版 wheel；`npm pack --dry-run` 仅包含预期启动器、运行时、postinstall、manifest 和 wheel |
| NPM-007 | 已完成 | `scripts/validate_npm_bootstrap.py` 在隔离 npm prefix/runtime 中完成真实 tarball 安装、两次 init、Codex TOML 解析、MCP `initialize/tools/list/knowledge_status` 和卸载；Windows CI 运行同一验证器 |

首发支持边界为 Windows 10/11 x64、Node.js 20+、npm 10+、Python 3.11+。本批不自动下载 Python，不重写 Python 核心或 CodeGraph，也不宣称 macOS/Linux npm 路径已完成发布验收。

## 当前检索质量交付复核（WP-RQ-01～04）

当前有效实施基线为 `docs/retrieval-quality-work-package.md`。WP-RQ-01～03 已完成 Phase 0～2 的数据模型、可观测性、多路召回和确定性符号优先排序；WP-RQ-04 的 RQ-P3-001 已完成显式 core/supporting/optional 分层契约，其他 Phase 3 需求仍按验收状态登记，不以已有字段或局部测试代替最终验收。

当前结论分为实践门和最终生产门。对真实 `D:\Github-Poj\gardenserver` 的收窄实践范围，32 条 Phase 0/1 题全部 verified，并已逐条显式复核 `acceptable_supporting_files`；原方案 `precision@5` 按最终 Top-5 与 `expected_files ∪ acceptable_supporting_files` 计算，精确入口/路径占比较高的 Phase 0 种子集为 `0.45`，20 条 Phase 1 实践集达到门槛 `0.50`。文件、核心文件和符号召回均为 `1.00`，nDCG@5 为 `0.938488/0.832547`。`core_file_precision=0.241667/0.32` 是 Core 层宽度诊断，不是原方案 Top-5 门槛，不能再据此单独判定实践不可用。

CodeGraph 调用优化通过请求级只读缓存、snapshot/status 复用、查询去重和高置信图锚点上限，将相同题集 hybrid P95 从 `19.93s/16.67s` 降至 `4.26s/4.48s`，且上述召回未下降。`evaluation/thresholds-gardenserver-phase1.json` 已按原方案执行机器门禁：召回、`precision@5`、nDCG@5 和 fallback 均通过，唯一失败项为 P95 高于 `1.5s`。因此当前版本可进入受控项目实践，但不能宣称正式性能门完成；主要剩余成本是公开 CLI 的进程启动和串行命令协议。最终生产门仍未通过：跨仓库专项数据仍只有 95 题、77 verified，gardenserver 仍只有一个独立快照；RQ-P2-007/RQ-P3-005 要求的 300 题、每类 30 题和 3 个稳定快照继续保留，不以此次实践结论冒充完成。

RQ-P3-002 采用“双源隔离”契约：运行时 `RequiredEvidencePlanner` 只消费已完成召回和排序的查询、Core 文件、符号及 CodeGraph `trace` 关系，不接收 Token 预算或评测标签；Dataset v2 的 `required_evidence` 仅作为评测 Oracle。关系路径按有序、连续的 `calls` 边整体匹配，符号保留稳定 ID、源码路径、签名和行号 span。预算组装依次裁剪 optional、低分 supporting、supporting 内容和其他 best-effort 诊断；最小 required 仍无法装入时严格不超预算，并返回 `context_incomplete=true`、稳定缺失 ID 和 `insufficient_for_required`。gardenserver 的真实 CodeGraph 1.5 索引已验证 `AccountApi.login -> AccountComponent.do_login` 的直接调用边能形成并保留 required path；动态或无法规范化的端点不会冒充必要路径。

RQ-P3-003 增加 `context_status` 状态契约，按 `context_incomplete`、`needs_source_check`、`low_confidence`、`complete` 优先级公开状态、置信度、复核原因和源码复核标志；RQ-P3-004 已完成 trace schema v2、阶段耗时/状态、CodeGraph 证据、裁剪前后 evidence 快照、逐步裁剪事件以及 lexical/CodeGraph/ranking/context-assembly percentile 和目标结果汇总。RQ-P3-005 已增加 `validate_evaluation_dataset.py`、strict-live provenance 和性能超标非零退出路径，但仍等待负责人确认的 300 题、每类 30 题和 3 个独立稳定快照，不以重复同一快照或合成题目填充门禁。

## 当前交付复核：WP-13 / CG-ONLY（承接 WP-11 / WP-11-HF）

本节是 0.1.30 的当前验收结论；下方较早版本的工作包和里程碑记录仅用于历史追溯。评测实测值只以 `evaluation/reports/latest.json` 为唯一来源，审计不复制可能随重跑漂移的指标快照。

| 需求 ID | 当前结论 | 验收边界 |
| --- | --- | --- |
| REL-001～REL-005 | 已完成 | `project-kb finalize` 区分源码提交、同步、生成物提交和只读检查；不执行 `git add/commit/push`，CI 使用 `--check` 验证最终边界 |
| CG-001～CG-004 | 已完成 | CodeGraph 1.5 公共 CLI 响应被规范化为统一引擎契约；不可用或响应不合法时明确报错，绝不以 builtin 冒充 |
| CG-005 | 已完成 | 临时四文件项目已通过真实 CodeGraph 1.5.0 的 `init/files/query/trace/impact/affected` 六项验证，源仓库未生成 `.codegraph` |
| CG-006 | 已完成 | `KnowledgeAPI` 在 SQLite 符号和关系为空时仍通过所选 CodeGraph 引擎返回实时事实，并由正负测试覆盖 |
| CG-ONLY-001～004 | 已完成 | 配置、Schema 和引擎工厂只接受 `codegraph`；BuiltinCodeIndexEngine 及本地 parser 已删除；旧 `engine: builtin` 返回结构化迁移错误；生成页不再发布本地路由或入口占位事实 |
| RET-001～RET-005 | 已完成 | 已修正陈旧锚点并实施分阶段证据选择、依赖优先、核心文件上限、Markdown 引用约束和 `selection_reasons` 可解释输出 |
| RET-006 | 已完成 | 40 题正式评测通过冻结绝对门槛；实测指标和可比回归结论见 `evaluation/reports/latest.json` |
| REL-006 | 已完成 | 默认配置和本项目配置排除 `.worktrees/**`，真实发现测试证明内部 Git worktree 不进入索引 |
| EVAL-001 | 已完成 | 0.1.28 使用与当前 40 题数据集相同哈希的冻结基线；正式报告记录干净工作区，绝对质量门和可比回归均通过，且无 `baseline_dataset_mismatch` |
| DOC-001 | 已完成 | 审计不再复制评测实测值，版本化 JSON 报告是唯一指标源；CI 校验器拒绝题集哈希不匹配的基线 |
| KNOW-001 | 已完成 | 来源变化的 curated/decision 已逐条人工复核；`stale_knowledge=0`、`conflicted_knowledge=0`，`finalize --check` 返回 `ready` |

当前限制：本仓库已切换为 `engine: codegraph`，运行时只接受真实 CodeGraph Adapter；CLI 不可用或项目未初始化时明确失败，不再回退到本地 parser。CodeGraph 仍不能证明动态分派、反射、运行时依赖注入等事实。真实业务项目覆盖率、查询性能和人工知识审核流程仍需继续扩展，不能因本轮 Adapter 通过而宣称最终产品目标已完成。

0.1.30 的 50 题全策略绝对质量门仍未通过：hybrid/code 的文件召回和核心精确率、Markdown 的文件召回与精确率仍低于冻结阈值；所有可用策略的 `ranking_fallback_rate` 为 0。该缺口保留为后续检索质量工作，不通过降低阈值或恢复 builtin 规避。

## 0. 需求重新对齐决议（0.1.21 起的唯一有效实施基线）

用户在 CodeGraph 适配验证后重新明确了产品目标：PKS 不重写 CodeGraph，而是在其代码事实之上建立由 MCP AI 客户端生成、用户审核并可增量维护的开发指导知识库。

当前唯一有效的近期实施范围是：

1. CodeGraph 负责代码事实和自身索引更新；
2. 接入 MCP 的 AI 客户端首次初始化时分批覆盖全项目，自动发现功能类别；
3. 用户先打开中文功能分类目录草稿审核，再分别审核轻量方法论和项目事实指导；方法论可在二次沟通后逐步完善，项目事实指导优先保证证据可靠；
4. 确认后的类别和指导进入 KnowledgeStore，并通过 MCP 查询；
5. 后续只分析变化代码及 CodeGraph 必要影响范围；
6. 一级事实变化自动更新，二级指导变化和三级分类变化必须重新审核；
7. PKS 不自建 watcher，不内置大模型，不建设复杂任务调度系统。

该范围的完整需求、架构、失败规则和验收标准见：

- docs/superpowers/specs/2026-08-12-ai-client-development-guidance-design.md
- docs/next-version-plan.md

上述两份文档从 0.1.21 起取代本报告原“0.1 最小需求收敛决议”，成为当前功能开发的直接基线。下方 0.1 节及更早版本记录保留为历史决议，不再单独代表当前交付状态；当前 P0 以 WP-10 复核节和下一版本计划为准。

## 0.1 历史范围收敛决议（0.1.15 阶段，已被取代）

用户已将需求收敛为两个结果：

1. 在本地代码项目中建立可查询的项目级知识库；
2. 代码修改、新增或删除后，自动更新代码索引和生成知识。

本节从 0.1.15 起取代下方历史审计中的扩大路线图。下方关于 CodeGraph、模型 Provider、Feature Guide、Proposal、多客户端、共享 daemon、发布流水线等内容仅作为历史实现记录，不再是当前开发目标或验收阻塞项。

### 0.1 必须完成

| ID | 最小需求 | 验收标准 | 当前状态 |
| --- | --- | --- | --- |
| LK-INIT-001 | 一条命令本地建库 | `project-kb init <project>` 创建 SQLite、Manifest 和知识文档 | 已完成 |
| LK-INIT-002 | 默认不依赖网络和大模型 | 首次建库只使用本地静态解析和确定性生成 | 已完成 |
| LK-INIT-003 | 默认生成中文文档 | index、project-map、module、test-map、entrypoints 使用中文标题和说明 | 已完成 |
| LK-INIT-004 | 来源可追踪 | generated knowledge 保留源码路径、符号或哈希来源 | 已完成 |
| LK-UPD-001 | 自动同步修改 | 运行 `project-kb watch` 后，修改源码会更新哈希、符号、关系和 generated knowledge | 已完成 |
| LK-UPD-002 | 自动同步新增和删除 | 新文件加入索引，删除文件及其自动知识从索引移除 | 已完成基础版 |
| LK-UPD-003 | 无变化不重建 | noop sync 返回 current，不原子重建数据库 | 已完成 |
| LK-UPD-004 | 过期可见 | 未同步文件进入 `pending_files`，旧知识正文不会冒充最新事实 | 已完成 |
| LK-REL-001 | 更新一致性 | 解析期间再次保存时采用最终内容和最终哈希 | 已完成 |
| LK-REL-002 | 单 watcher | 同一项目不允许两个 watcher 同时写入 | 已完成 |
| LK-REL-003 | 失败可发现 | crashed/error、PID、heartbeat 和日志可查询 | 已完成 |
| LK-REL-004 | 保留有效索引 | 首次/重建使用临时数据库，成功后原子替换 | 已完成 |
| LK-SCALE-001 | 中大型项目可完成建库 | 至少一个 1000+ 文件真实项目完成只读初始化 | 已完成：2,887 文件 Lua/Skynet 镜像 |
| LK-SCALE-002 | 大项目更新可用 | 变更正确同步；允许当前轮询较慢，但不能产生错误知识 | 部分完成：正确性已验证，性能待优化 |
| LK-DOC-001 | 使用和审计文档默认中文 | 面向用户的生成文档和主说明使用中文 | 已完成 |

“自动更新”的当前定义是：用户显式运行前台 `project-kb watch <project>` 后自动同步。本期不实现系统服务、开机自启或共享后台 daemon。

### 0.2 明确不做

除非用户以后重新提出明确需求，否则不继续开发：

- 真实外部 CodeGraph Adapter；
- 云端或本地大模型 Provider；
- 自动业务语义、Feature Guide、Workflow、Recipe；
- Proposal、多人审核和 Git PR 知识治理；
- 向量检索；
- Claude、Cursor、Gemini 真实版本矩阵；
- 多客户端共享 daemon；
- 正式签名、上传和发布流水线；
- Lua/Skynet 动态运行时推理和业务标准答案；
- “AI 完成功能开发”的端到端成功率评测。

现有扩展代码可以保留以维持兼容性，但不得为了完善这些范围外能力主动增加复杂度。

### 0.3 当前证据

| 能力 | 源码 | 自动化证据 |
| --- | --- | --- |
| 初始化、原子建库 | `ProjectService.initialize`、`_atomic_rebuild` | `IntegrationTests.test_init_sync_freshness_retrieval_and_mcp` |
| 哈希增量同步 | `ProjectService.sync` | APP_V1→APP_V2 与删除文件集成测试 |
| watcher 自动同步 | `ProjectService.watch` | `test_watch_once_automatically_refreshes_code_and_generated_knowledge` |
| 保存竞态保护 | `ProjectService._parse_stable` | `test_sync_rechecks_source_hash_and_keeps_final_snapshot_consistent` |
| 单 watcher 与死 PID 恢复 | watcher lock | `test_single_watcher_coordinator_rejects_duplicate_and_recovers_dead_owner` |
| 崩溃和分支状态 | `status`、`watcher_health` | crashed watcher 与 branch transition 测试 |
| 中文生成知识 | `KnowledgeGenerator` | 集成测试检查“项目地图”“项目知识库”“位于”等文本 |
| 真实大项目 | 只读镜像流程 | 2,887 文件、约 50 万行 Lua/Skynet 项目完成初始化 |

0.1.15 收口回归：`PYTHONPATH=src python3 -m unittest discover -s tests -q`，81 项测试全部通过。

### 0.4 当前完成度和剩余工作

核心需求完成度约为 **95%**。

唯一仍在需求范围内的改进是大型项目轮询效率：

- watcher 每轮仍会遍历候选文件并读取内容计算哈希；
- 5,000 文件 noop sync P95 历史基线约 38.8 秒；
- 这会让反馈变慢，但不会导致知识错误。

该项为可选 P1，估算 2～4 人日。只允许采用最小优化：缓存 `mtime + size` 筛选疑似变化文件，并保留周期性全量哈希校验；不引入共享 daemon、复杂事件总线或新的外部依赖。

### 0.5 最小验收命令

```bash
PYTHONPATH=src python3 -m unittest tests.test_integration tests.test_watch_wp07
PYTHONPATH=src python3 -m project_knowledge --version
PYTHONPATH=src python3 -m project_knowledge sync . --json
PYTHONPATH=src python3 -m project_knowledge status . --json
PYTHONPATH=src python3 -m project_knowledge check . --json
```

真实项目验收：

1. 执行 `project-kb init <project>`；
2. 启动 `project-kb watch <project>`；
3. 修改一个函数名，等待一个 watcher 周期；
4. 确认 `pending_files` 为空且 generated module 出现新函数名；
5. 删除该文件，再等待一个周期；
6. 确认文件和对应 generated module 从索引移除。

### 0.6 后续开发约束

以后任何修改必须对应一个 `LK-*` 需求 ID。不能对应的功能视为范围外，不实施。每批仍须先补测试、只实现最小行为、补丁版本递增一次、更新 CHANGELOG、同步 generated knowledge，并报告 curated/ADR 是否需要人工复核。

---

## 历史扩大范围审计（仅供追溯，不再作为当前验收基线）

# Project Knowledge System 需求对齐审计与后续实施基线

> 审计日期：2026-08-06  
> 审计基线版本：0.1.0  
> 报告建立版本：0.1.1  
> 原始需求：`docs/project-knowledge-system-design.md`  
> 审计对象：当前仓库源码、测试、插件、知识文档和离线评测集  
> 报告状态：Approved implementation baseline  

## 1. 报告目的

本报告将原始系统设计逐项映射到当前实现，识别已完成、部分完成、未完成和实现偏离的能力，并把差距转换为可执行工作包。

后续开发必须以本报告为实施基线。出现以下情况时必须更新本报告：

1. 原始产品目标发生变化；
2. 工作包验收标准发生变化；
3. 新增影响总体架构的实现决策；
4. 真实项目评测暴露新的阻断问题；
5. 某项能力被明确移出范围。

本报告不是发布宣传材料。所有“完成”判断必须有源码、测试或评测证据；没有证据的能力一律标记为“未验证”或“部分完成”。

## 2. 审计结论

### 2.1 总体判断

当前实现完成了可信代码事实层的基础骨架，在 0.1.5 建立受控语义草案闭环，并在 0.1.6 完成草案到 curated 的 Proposal 审核基础闭环：

- 本地 CLI 生命周期；
- 可重建 SQLite 索引；
- 基础结构解析；
- 最小 Generated Knowledge；
- 来源哈希和部分新鲜度状态；
- 五个只读 MCP 工具；
- Codex Skill 与 Plugin；
- 小型合成仓库测试；
- 基础检索评测工具。
- Feature Guide、Workflow、Recipe 严格 Schema；
- EvidencePack 到中文语义草案的 Fake Provider 端到端生成；
- 文件、符号、路径、行号、哈希与文档候选权威的二次校验；
- 按功能分片的 draft 存储、索引、检索和来源变化过期传播。
- 稳定 Proposal ID、结构化 Patch operation 与严格 Schema；
- ChangeSet 驱动的 Semantic Update Queue；
- Feature Guide 草案到 curated generated block 的显式审核提升；
- apply/reject 审核记录、目标和来源哈希复核、冲突冻结与幂等应用；
- generated block 精确更新/删除、删除和 supersedes 证据；
- ADR 只追加中文草案且禁止改写已有决策。

当前实现尚未完成原始产品愿景中的核心用户价值：

> 针对一个具体功能开发任务，返回模块职责、当前工作流、推荐扩展点、业务不变量、参考实现、影响范围和验证方式。

当前 `knowledge_context` 已能优先命中存在的 Feature Guide，并返回职责、流程、扩展点、Recipe、测试和 unknowns；Feature Guide 草案可以通过来源锁定的 Proposal 显式审核并进入 curated generated block。但真实项目尚未批量生成并经业务负责人确认这些指南，任务分类、参考实现和多跳功能影响已在 WP-06 形成可测试的本地 MVP；真实 Lua/Skynet 业务答案、动态运行时行为和负责人确认仍未完成。因此当前产品应定义为：

> Source-traceable code-fact MVP + 可审核的 feature semantic knowledge MVP，而不是完整的 feature-development knowledge system。

### 2.2 完成度判断

| 范围 | 判断 |
| --- | --- |
| 原始设计的完整产品愿景 | 未完成 |
| 第 25 节事实型 MVP 骨架 | 部分完成 |
| 第 32 节第一轮开发起点 | 大部分完成；真实结构/性能基线已建立，CodeGraph 与业务答案评测未完成 |
| 功能开发指导 | 已完成草案生成、审核提升与 WP-06 检索指导 MVP；真实项目批量生成、业务验证和动态运行时分析未完成 |
| 语义知识演进闭环 | 已完成本地 Proposal 基础闭环；自动语义生成触发、Git/PR 团队协作和批量审核未完成 |
| 真实大型 Lua/Skynet 项目可用性 | 已验证可只读初始化，但查询延迟和业务指导能力未达可用 |
| 安全的云端/本地模型接入 | 安全执行边界已完成；生产 Provider 和组织级外发决策未完成 |
| 团队级 Git/worktree/daemon 生命周期 | 部分完成 |

### 2.3 最高优先级缺口

1. WP-06 已补齐任务分类、参考实现、多跳影响和排序解释；剩余缺口是 Lua/Skynet 真实业务答案、动态运行时行为和负责人确认；
2. Feature Guide 生成/审核基础闭环已完成，但真实 Lua/Skynet 项目尚未批量生成并由负责人验证；
3. 没有真正的 CodeGraph Adapter；
4. Lua/Skynet 已有离线证据适配，但运行时语义、SVN revision 和业务边精度仍未完成；
5. 检索精确率仍需持续优化；40 题结构基线已建立，WP-01/WP-02 另有 5 题补充集，但缺少经负责人确认的真实业务答案和端到端功能开发评测；
6. Proposal 尚未接入生产模型自动触发、Git PR 审核和批量队列治理；
7. Git、watcher、daemon、并发和异常恢复没有达到设计要求；
8. 配置迁移和结构化日志未实现；性能基线已建立，但 5000 文件延迟远未达标。

## 3. 审计范围与方法

### 3.1 审计输入

- `docs/project-knowledge-system-design.md`
- `src/project_knowledge/**`
- `tests/**`
- `plugins/project-knowledge/**`
- `evaluation/questions.jsonl`
- `.project-kb/schemas/**`
- `docs/knowledge/**`
- 当前知识库状态和离线评测输出

### 3.2 判断等级

| 状态 | 定义 |
| --- | --- |
| 已完成 | 有实现和正向/负向测试，行为符合原始需求 |
| 部分完成 | 存在可运行骨架，但覆盖、可靠性或产品行为不完整 |
| 未完成 | 只有设计、字段或占位符，或完全没有实现 |
| 未验证 | 可能具备能力，但没有满足需求规模和场景的证据 |
| 实现偏离 | 当前行为与原始需求承诺不一致或容易产生误导 |

### 3.3 审计原则

- 字段存在不等于功能完成；
- 配置可读写不等于配置生效；
- 小型合成测试不等于真实项目验收；
- 接口存在不等于上下文质量达标；
- 枚举存在不等于状态机完整；
- 目录存在不等于工作流实现；
- 能生成文档不等于能指导功能开发。

## 4. 当前实现基线

### 4.1 已实现的主要组件

| 组件 | 当前实现 |
| --- | --- |
| Core CLI | init、sync、rebuild、watch、status、check、install、uninstall、doctor、mcp、evaluate、generate、feature-candidates |
| 索引引擎 | BuiltinCodeIndexEngine；Python AST；其他语言通用正则 |
| 存储 | SQLite、WAL、事务、FTS5、来源和查询统计 |
| 知识生成 | project-map、module-map、routes、test-map、Feature Guide draft、index |
| 人工知识 | architecture、conventions、glossary、ADR 文件扫描 |
| 新鲜度 | fresh、potentially_stale、stale 的部分转换 |
| MCP | context、search、get、impact、status |
| 集成 | AGENTS marker、Codex Plugin、Skill、post-task sync hook |
| 评测 | JSONL 问题集和文件/符号 precision/recall |
| 版本 | 单一版本源、补丁版本递增、CHANGELOG |

### 4.2 当前自动知识集合

当前确定性生成：

- 项目地图；
- 顶层模块地图；
- 静态路由表；
- 测试文件地图；
- 总知识索引。

当前经 Provider 生成并在落库前校验：

- 按功能分片的 Feature Guide；
- 内嵌 Workflow；
- 内嵌开发、验证和回滚 Recipe；
- 无来源结论的 unknowns。

未生成：

- 项目级完整功能地图；
- 经人工验证的业务工作流；
- 服务拓扑；
- 数据模型；
- 配置项索引；
- 消息和协议地图；
- 调度器和消费者地图；
- 经人工验证的推荐扩展点、开发 Recipe 和业务不变量；
- 变更历史和功能演进摘要。

### 4.3 当前评测基线

0.1.1 的历史评测集只有 3 个本仓库问题：

| 指标 | 当前结果 |
| --- | ---: |
| 文件召回率 | 0.8333 |
| 文件精确率 | 0.0917 |
| 符号召回率 | 0.5000 |
| 符号精确率 | 0.0500 |
| 平均上下文 Token | 3251.33 |

结论：

- 系统能碰到部分正确文件；
- 返回文件和符号噪声过高；
- 对“防止返回过期内容”问题，符号召回率为 0；
- 尚无扩展点、不变量、功能流程和开发步骤类评测；
- 尚无 grep + Read、只有代码图、只有 Markdown 的对照结果。

0.1.3 已将快速集扩充为 20 条经当前源码复核的中文开发问题。评测报告和冻结基线已排除出索引，避免历史结果污染后续运行。稳定轮次结果如下：

| 策略 | 文件召回 | 文件精确 | 符号召回 | 不变量召回 | 设计原因召回 | 成功率 | 平均 Token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| hybrid | 0.958333 | 0.087478 | 0.833333 | 0.142857 | 1.000000 | 0.45 | 1883.60 |
| only-code | 0.941667 | 0.318932 | 0.833333 | 0.000000 | 0.000000 | 0.45 | 591.25 |
| grep + Read | 0.675000 | 0.293750 | 0.000000 | 0.000000 | 0.000000 | 0.00 | 6258.40 |
| only-Markdown | 0.933333 | 0.087313 | 0.000000 | 0.428571 | 1.000000 | 0.00 | 15937.05 |
| only-codegraph | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 | 不可用 |

补充指标：hybrid 和 only-code 的调用路径召回均为 `1.0`、扩展点召回均为 `0.5`；generated 来源覆盖率为 `0.9`。only-codegraph 明确返回 `adapter_unavailable`，没有用 builtin 冒充。该结果证明当前工具能提供结构锚点，但低精确率、低不变量召回、Markdown 高 Token 和缺少端到端开发成功率仍然阻止产品验收。

0.1.4 将快速集扩展到 25 题，并加入 Provider/EvidencePack/授权/dry-run/扩展点问题。未降低 0.1.3 的指标阈值，最低样本数从 20 提升到 25。通过轮次如下：

| 策略 | 文件召回 | 文件精确 | 符号召回 | 扩展点召回 | 成功率 | 平均 Token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hybrid | 0.966667 | 0.083771 | 0.813333 | 1.000000 | 0.52 | 1975.40 |
| only-code | 0.933333 | 0.259030 | 0.813333 | 1.000000 | 0.52 | 691.92 |
| grep + Read | 0.726667 | 0.305000 | 0.000000 | 0.000000 | 0.00 | 6443.04 |
| only-Markdown | 0.933333 | 0.098372 | 0.000000 | 0.000000 | 0.00 | 3102.24 |

0.1.4 修复了不同数据集汇总值被错误比较、only-Markdown 不遵守总 Token 预算、精确符号被模糊命中淹没三个评测/检索问题。与 0.1.3 数据集哈希不同时只执行绝对门；冻结 0.1.4 后再对同一数据集执行相对回归。

0.1.5 将快速集扩展到 30 题，新增 Feature Guide Schema、语义生成、来源校验、草案生命周期和功能检索五类问题。未降低冻结阈值；为消除真实上下文浪费，only-code 不再重复序列化 call path/summary，grep + Read 最大候选读取从 8 个收紧为 7 个。最终冻结轮次：hybrid 文件/符号召回 `0.972222/0.844444`、成功率 `0.6`、平均 `1507.5` Token；only-code 文件/符号召回 `0.944444/0.844444`、平均 `190.166667` Token；grep + Read 文件召回/精确率 `0.719444/0.316666`；only-Markdown 文件召回/精确率 `0.922222/0.107086`，生成来源覆盖保持 `0.9`。

## 5. 原始需求追踪矩阵

### 5.1 产品目标

| ID | 原始需求 | 状态 | 当前证据 | 差距 |
| --- | --- | --- | --- | --- |
| PG-001 | 一条命令初始化知识库 | 已完成基础版 | `project-kb init`；2881 文件 Lua/Skynet 只读镜像初始化成功 | 原目录写入未授权；初始化约 128.6 秒，性能未达产品目标 |
| PG-002 | 支持中大型长期维护仓库 | 已验证可运行，未达可用目标 | 500/5000 harness 与 2881 文件真实镜像 | 5000 文件 context P95 约 141 秒；大型模块只披露截断，尚未分片 |
| PG-003 | 代码事实增量更新 | 部分完成 | 文件哈希、sync、watch | 每次仍遍历并读取全部文件；保存竞态未重排队 |
| PG-004 | 文档与源码来源映射 | 部分完成 | source marker、source hashes；Feature Guide 逐陈述文件/符号/行/哈希校验 | 确定性模块页仍以文件级来源为主；自然语言与引用的语义蕴含仍需人工审核 |
| PG-005 | 返回最小任务上下文 | 部分完成 | Token 预算、最多四页、Feature Guide 类型优先和中文标题包含匹配 | 评测精确率低；任务分类和真实业务验证缺失 |
| PG-006 | 多 AI 客户端 | 部分完成 | MCP、Codex Plugin | Claude、Cursor、Gemini 适配器未实现 |
| PG-007 | 核心本地运行 | 已完成 | 无运行时网络依赖 | 云能力尚未实现 |
| PG-008 | Git 分支/worktree/协作 | 部分完成 | Git 状态和 commit 元数据 | checkout/rebase/worktree 补偿和测试缺失 |
| PG-009 | 自动检测知识过期 | 部分完成 | 来源哈希状态、pending 来源屏蔽、content/commit 分离 | conflicted、rename 和复杂 Git 状态仍未完整覆盖 |
| PG-010 | 通过评测量化效果 | 已完成首个基线，产品验收未完成 | 20 题、四个可用策略、真实镜像、500/5000 性能、CI 门 | 真实 codegraph、业务标准答案和端到端修改成功率缺失 |

### 5.2 核心设计原则

| ID | 原始原则 | 状态 | 差距 |
| --- | --- | --- | --- |
| PR-001 | 事实、解析关系、AI 推断、人工知识分离 | 已完成基础版 | generated/draft/curated/decision 已分层；语义草案只可通过 Proposal 审核进入 curated generated block |
| PR-002 | 每条知识来源可追踪 | 已完成草案基础版 | Feature Guide 每个确定性陈述强制来源；unknowns 单独记录 | 语义正确性仍需人工审核，其他 generated 页尚非逐陈述来源 |
| PR-003 | 渐进式披露 | 部分完成 | Token 裁剪存在，但检索噪声高 |
| PR-004 | 自动化程度与可信度匹配 | 已完成基础版 | generated 自动覆盖；draft 需显式 apply；curated 人工正文和已有 ADR 禁止自动改写 |
| PR-005 | Git 为协作基线 | 部分完成 | 文档可提交；分支、合并和 Manifest 冲突策略未验证 |
| PR-006 | 核心独立于客户端 | 已完成 | CLI、模型、存储和 MCP 未依赖 Codex |

### 5.3 交付形态

| ID | 交付物 | 状态 | 差距 |
| --- | --- | --- | --- |
| DL-001 | Core CLI | 部分完成 | init/sync/rebuild/status/check/install/uninstall/doctor/migrate/watch/MCP/evaluate/generate/propose/apply/reject 已完成；仍缺独立 benchmark/enrich 命令 |
| DL-002 | Knowledge MCP | 已完成基础版 | 缺写提案工具；没有共享 daemon |
| DL-003 | Workflow Skill | 已完成基础版 | 任务结果和测试证据没有写入 ChangeSet |
| DL-004 | Codex Plugin | 已完成基础包 | 缺安装兼容性和发布验证 |
| DL-005 | 其他客户端适配器 | 已完成基础版 | Claude/Cursor/Gemini 所有权标记安装与卸载已实现；缺真实客户端版本端到端测试 |

### 5.4 初始化流程

| ID | 原始要求 | 状态 | 差距 |
| --- | --- | --- | --- |
| IN-001 | 环境和仓库检查 | 部分完成 | doctor 基础检查；无规模风险、引擎可用性、Git 策略诊断 |
| IN-002 | 语言、框架、包管理器识别 | 部分完成 | 语言识别；框架和包管理器识别有限 |
| IN-003 | 构建、测试、格式化命令识别 | 部分完成 | 只按文件名推断一个测试命令 |
| IN-004 | 服务入口、路由、任务和消费者识别 | 部分完成 | Python 路由；其他入口、调度器、消费者缺失 |
| IN-005 | Schema、迁移和配置识别 | 未完成 | 配置文件仅入库为空 ParseResult；无数据模型 |
| IN-006 | 已有 README、ADR、API 文档识别 | 未完成 | 不导入、不分类、不建议转 curated |
| IN-007 | 框架感知结构索引 | 已完成基础版 | 只消费真实 CodeGraph 的 `snapshot/search_symbols/get_source`；首批覆盖 FastAPI、Flask、Django、Lua/Skynet，并输出来源、置信度和 unknowns |
| IN-008 | 低置信和解析失败报告 | 部分完成 | 有计数和 confidence；缺按风险排序 |
| IN-009 | 初始化建议人工确认问题 | 部分完成 | 只有通用建议，不生成项目特定问题 |

### 5.5 代码结构引擎

| ID | 原始要求 | 状态 | 差距 |
| --- | --- | --- | --- |
| EN-001 | 可替换 CodeIndexEngine | 部分完成 | 接口只有 discover/parse/status，低于设计接口 |
| EN-002 | 真正的 CodeGraph Adapter | 已完成 | 仅使用真实 `codegraph-public-cli`，无 builtin fallback；已通过真实 CodeGraph 1.5.0 验证 |
| EN-003 | searchSymbols/getSource/trace | 已完成基础版 | Adapter 与 MCP 主链路已公开三项能力；公开符号身份统一为 `path::qualifiedName` |
| EN-004 | impact/affectedTests 引擎能力 | 部分完成 | 简单一跳关系和测试路径启发式 |
| EN-005 | 关系可信度 | 已完成基础版 | 缺按解析器能力校准和真实精度评测 |
| EN-006 | 动态边界报告 | 已完成基础版 | 只有通用 limitations，缺项目级缺口 |
| EN-007 | Lua/自定义框架覆盖 | 未完成 | 已被原始设计列为待验证问题 |

### 5.6 知识分层和模型

| ID | 原始要求 | 状态 | 差距 |
| --- | --- | --- | --- |
| KN-001 | Generated Knowledge | 部分完成 | 只完成最小四类页面 |
| KN-002 | Curated Knowledge | 已完成审核基础版 | 文件托管、新鲜度、Feature Guide 草案、generated block 审核提升已完成；批量/PR 审核未实现 |
| KN-003 | Decisions/ADR | 已完成基础保护 | 可索引且 Proposal 只允许新增中文草案；尚无编号分配与接受/废弃状态命令 |
| KN-004 | Feature Guide | 已完成基础版 | 严格 Schema、Provider 生成、逐来源校验、分片、检索和 Proposal 提升已有测试；真实业务验证未完成 |
| KN-005 | Workflow | 已完成 Feature Guide 内嵌基础版 | 连续步骤和逐步来源已验证；独立工作流页尚未生成 |
| KN-006 | Recipe | 已完成 Feature Guide 内嵌基础版 | 开发、验证、回滚结构已验证；独立 Recipe 页尚未生成 |
| KN-007 | generated block | 已完成 | 精确 upsert/delete，只修改命名 block，人工正文回归测试保留 |
| KN-008 | supersedes 生命周期 | 部分完成 | 删除 operation 与 ADR 草案可记录 supersedes；尚未同步回 KnowledgeRecord 生命周期 |
| KN-009 | inferred 生成 | 未完成 | 枚举存在但从不产生 |
| KN-010 | conflicted 检测 | 部分完成 | Proposal 目标或来源变化会冻结为 conflicted；KnowledgeRecord 级冲突仍未完整实现 |
| KN-011 | 来源至少一种 | 部分完成 | generated 多为文件来源；空模板无来源 |
| KN-012 | 来源 commit/task/decision | 未完成 | SourceReference 支持，但生成流程基本不用 |

### 5.7 增量更新和语义提案

| ID | 原始要求 | 状态 | 差距 |
| --- | --- | --- | --- |
| UP-001 | watcher 文件级更新 | 部分完成 | 轮询全量 discover，不是事件合并 |
| UP-002 | 防抖和事件合并 | 部分完成 | sleep 间隔，不是真正事件批次 |
| UP-003 | 文件保存不调用 LLM | 已完成 | 当前没有 LLM |
| UP-004 | ChangeSet | 部分完成 | tests_run/results/author 未采集 |
| UP-005 | Git hook 触发 | 已完成基础版 | 管理 `post-checkout/post-merge/post-rewrite/post-commit`，保留用户 hook，并支持 linked worktree |
| UP-006 | checkout/merge/rebase 补偿 | 已完成基础版 | `git-event` 状态机覆盖 checkout、merge、rewrite、detached HEAD 和非祖先 reset；失败暴露为 `reconciliation_required` |
| UP-007 | 语义更新队列 | 已完成基础版 | sync 将 ChangeSet 写入稳定队列项，关联 Proposal 后更新状态；尚无批量治理和自动模型 worker |
| UP-008 | 生成 Knowledge Proposal | 已完成基础版 | 稳定 ID、严格 Schema、手工 Patch 与 Feature Guide 草案入口；尚无生产模型自动建议 |
| UP-009 | 审核、应用、拒绝 | 已完成 | CLI/API 支持 dry-run/json/quiet，记录审核人、时间、理由、状态与结果哈希 |
| UP-010 | ADR 追加草案 | 已完成 | 只允许不存在的 decisions 路径和 append_adr_draft；强制中文草案状态，已有 ADR 拒绝 |
| UP-011 | 删除知识需要替代证据 | 已完成基础版 | delete_generated_block 强制 deleted_sources 与 supersedes，只删除命名 block |

### 5.8 检索和 MCP

| ID | 原始要求 | 状态 | 差距 |
| --- | --- | --- | --- |
| RT-001 | 五个只读 MCP 工具 | 已完成 | 基础协议兼容已实现 |
| RT-002 | FTS/BM25 | 已完成基础版 | FTS 可用时启用 |
| RT-003 | 可选向量检索 | 已完成基础版（0.1.33） | 默认 disabled；local provider、SQLite 向量索引、失效与 fallback、受约束 hybrid 召回已实现 |
| RT-004 | 符号和路径精确检索 | 部分完成 | 名称 LIKE；无公开 trace/getSource |
| RT-005 | 代码图遍历 | 部分完成 | 仅有限一跳扩展 |
| RT-006 | 任务类型和模块过滤 | 部分完成 | module tag 和简单词项；无任务分类 |
| RT-007 | 新鲜度和可信度加权 | 已完成基础版 | 权重未通过离线评测校准 |
| RT-008 | 返回来源、缺口、下一步 | 已完成基础版 | 摘要和 next_step 仍较通用 |
| RT-009 | 功能开发上下文 | 部分完成 | Feature Guide 可返回职责、流程、不变量、扩展点、Recipe、测试、陷阱和 unknowns | 缺参考实现、任务分类、多跳影响和真实 verified 功能指南 |
| RT-010 | 紧凑准确 | 未达标 | 当前 precision 低 |

### WP-12A：检索精确率与核心证据重排（0.1.29）

**实施状态：实现与聚焦测试已完成；绝对质量门尚未完成。**

本批次新增 50 条冻结评测（原 40 条答案逐项保护，新增 10 条 ranking hard-negative），并实现统一 `policy-v1` 文件排序、核心/辅助证据分区、fallback 质量门、严格 core 指标和 nDCG。`tests/test_evaluate.py`、`tests/test_ranking.py`、`tests/test_retrieval_wp06.py` 聚焦验证共 55 项通过。

clean source 与 clean index 对齐后的 0.1.29 本地模块评测记录：hybrid file/core recall `0.818333/0.788333`、core precision `0.312`、平均上下文 `1055.12`；code file recall `0.791667`、成功率 `0.38`；Markdown file recall/precision `0.573333/0.12`；grep recall/precision `0.815/0.326762`，所有策略 fallback 均为 `0.0`。因此 RT-010 仍标记“未达标”，剩余 8 个质量门失败（hybrid 4 项、code 2 项、Markdown 2 项）。不得冻结失败报告为发布基线，也不能降低阈值宣称完成。

审计结论：精确率改进已落地在统一排序与证据 provenance，而召回缺口仍主要来自候选覆盖和 Markdown 知识页来源质量；真实 CodeGraph Adapter 仍明确保持 `adapter_unavailable` 边界。

### 5.9 配置、并发、安全和可观测性

| ID | 原始要求 | 状态 | 差距 |
| --- | --- | --- | --- |
| OP-001 | 配置版本和 Schema | 已完成基础版 | config-v1 JSON Schema 已发布并允许扩展字段；复杂条件约束仍需扩充 |
| OP-002 | 配置向前迁移 | 已完成基础版 | v0→v1 支持 dry-run 并保留未知字段；尚无多跳迁移、自动备份和回滚 |
| OP-003 | SQLite WAL | 已完成 | 已启用 |
| OP-004 | 单写入协调者 | 已完成基础版 | 文件锁；无共享 daemon |
| OP-005 | 多客户端共享 daemon | 未完成 | 每进程独立 |
| OP-006 | 查询一致快照 | 部分完成 | SQLite 读事务；无并发集成测试 |
| OP-007 | 重建原子切换 | 已完成 | 临时 DB + os.replace |
| OP-008 | 过期锁恢复 | 已完成基础版 | watcher 协调锁具有 PID 生存检查和死进程恢复测试；通用写锁仍主要按租约年龄 |
| OP-009 | 索引中再次修改重排队 | 已完成基础版 | 解析前后二次哈希并重试；高频事件队列和真正事件驱动合并仍未实现 |
| OP-010 | 默认本地和无遥测 | 已完成 | Provider 默认 disabled/禁网/local_only；遥测保持关闭 |
| OP-011 | Secret 脱敏 | 已完成基础版 | 路径拒绝、赋值/Bearer/已知 Token/私钥和响应字段脱敏已有测试；扫描不能替代人工 dry-run 复核 |
| OP-012 | 云 Provider 授权和预览 | 已完成安全边界 | dry-run 不可执行；本地 loopback 集成测试通过；非本机要求 HTTPS、关闭 local_only 和精确授权；生产模型等待 D-001/D-002 |
| OP-013 | 生成模型版本记录 | 已完成基础版 | GenerationResult、缓存、检查点和 Feature Guide 结构化分片记录 provider/model/prompt/schema/evidence/request 哈希；Markdown 进入 Manifest |
| OP-014 | 结构化分类日志 | 已完成 watcher 基础版 | watcher start/error/stop 写入 JSONL；查询、Provider、提案和发布日志分类仍不完整 |
| OP-015 | 完整 status 指标 | 已完成基础版 | watcher_health、PID、heartbeat、coordinator、branch/commit 对齐已报告；共享 daemon 指标仍缺失 |

### 5.10 测试、性能和验收

| ID | 原始要求 | 状态 | 差距 |
| --- | --- | --- | --- |
| QA-001 | 配置解析测试 | 已完成基础版 | v0→v1 dry-run/apply、未知字段保留和 Schema 发布已覆盖；复杂非法 YAML 与多跳迁移仍缺 |
| QA-002 | Manifest Schema 测试 | 已完成基础版 | KnowledgeRecord、ChangeSet、Proposal 和 Manifest 落盘前执行运行时 Schema 验证；复杂负例仍需扩充 |
| QA-003 | 新鲜度状态机测试 | 部分完成 | generated、curated、Feature Guide draft 与 Proposal 来源/目标冲突均有覆盖 | 无符号删除、rename 全覆盖 |
| QA-004 | generated block 测试 | 已完成 | 覆盖替换、删除、人工正文保护、dry-run 和幂等 |
| QA-005 | ChangeSet 测试 | 部分完成 | 只在集成流程间接覆盖 |
| QA-006 | Token 裁剪测试 | 已完成基础版 | 无复杂多语言边界 |
| QA-007 | Secret 脱敏测试 | 已完成基础版 | 输入、响应、preview、缓存和检查点不泄漏测试 Secret；仍需扩充真实凭据规则库 |
| QA-008 | Git 状态转换测试 | 部分完成 | branch 切换补偿和 commit/content 对齐已覆盖；worktree/rebase/detached HEAD 仍缺 |
| QA-009 | 真实小型仓库集成 | 未完成 | 使用动态合成仓库，不是真实样本 |
| QA-010 | rename/worktree/multi-MCP | 未完成 | 无测试 |
| QA-011 | 索引竞态和崩溃恢复 | 已完成基础版 | 保存期间二次哈希、重复 watcher、死 PID 和错误健康状态已覆盖；进程级 kill/restart 仍缺 |
| QA-012 | 提案审核流程 | 已完成基础版 | 覆盖稳定生成、Feature Guide 提升、apply、reject、冲突、ADR、删除与 CLI；缺 Git PR 多人审核 |
| QA-013 | 500/5000 文件性能 | 已建立基线，性能未达目标 | `evaluation/reports/performance-0.1.3.json`；5000 文件上下文 P95 约 141 秒 |
| QA-014 | P95 指标 | 已完成基线 | 初始化、status、context、noop sync 均记录 P50/P95/P99 |
| QA-015 | 100% generated 来源覆盖 | 已测量，未达标 | 0.1.4 稳定快速基线为 0.9，质量门阻止继续下降 |
| QA-016 | 真实问题评测 | 部分完成 | 本仓库 30 条答案已按源码核验；Lua/Skynet 20 条业务候选等待 D-007 |
| QA-017 | grep/图/Markdown 对照 | 部分完成 | grep + Read、builtin code、Markdown 已有基线；真实 codegraph Adapter 不可用并明确报告 |
| QA-018 | 最终任务成功率 | 已测量基线，未达产品验收 | 当前测量结构锚点成功率；尚无真实代码修改端到端成功率 |

## 6. 关键实现偏离和缺陷

### 6.1 `engine: codegraph` 配置曾具有误导性（已止血，Adapter 未完成）

初审时 `create_engine` 接受 `builtin` 和 `codegraph`，但两者都返回 `BuiltinCodeIndexEngine`。0.1.2 起选择 `codegraph` 会明确失败；0.1.3 评测同样报告 `adapter_unavailable`，不再伪装能力。

- 已完成：在 CodeGraph Adapter 完成前，选择 `codegraph` 明确报错。
- 未完成：由独立 Adapter 实现设计接口，并让 doctor/status 显示 Adapter 版本和真实能力。

### 6.2 commit 基线曾不闭环（基础修复完成，复杂 Git 状态待补）

审计时：

- HEAD：`991f7ba1a25c706ad9002ebdf85ea4f0bf89f947`
- index commit：`042d4ea63aa29a9f64b8bcb548b2b45681555188`
- pending files：空

0.1.2 已将 content freshness 与 commit alignment 分开报告，并允许无内容差异同步补偿提交基线。剩余要求：

- 增加 checkout、merge、rebase、detached HEAD 测试。

### 6.3 curated 初始可信度曾过高（基础修复完成）

初审时空模板被作为 `verified/fresh` 记录。0.1.2 起保留 `project-kb:template` 的模板只能是 `inferred`，人工替换模板内容并建立来源后才可成为 verified；完整 Proposal 审核链路仍属于 WP-04。

剩余要求是实现受控审核，保证只有人工确认或通过 Proposal 的内容才能成为 `verified`。

### 6.4 模块页曾静默截断（披露已修复，分片未实现）

当前单模块仍有展示上限，但 0.1.2 起会显示总量、展示数量和继续查询入口，不再静默遗漏。剩余要求：

- 分层模块；
- 分片或分页；
- context 按任务读取相关分片，不读取整个模块。

### 6.5 配置字段无执行逻辑（告警已补，行为仍缺失）

`curated_mode`、`proposal_trigger`、`embeddings`、`telemetry` 和部分 `local_only` 只是可序列化字段。

0.1.2 起 doctor/check 会逐项报告未接线配置，不再让默认成功掩盖能力缺失。剩余要求：

- 每个配置项必须有行为测试。

### 6.6 检索评测显示高噪声

0.1.4 稳定 hybrid 基线文件 precision 为 `8.3771%`、符号 precision 为 `42.2119%`、不变量召回仅 `14.2857%`。精确符号排序已减少一部分噪声，但文件噪声和语义召回仍不合格。原因包括：

- 宽泛模块页携带全部模块文件来源；
- 符号 LIKE 检索缺少字段权重；
- 影响分析一跳扩展容易放大；
- 没有任务分类和 Feature Guide；
- 项目地图、测试和插件符号容易进入无关任务。

要求：

- 先扩充评测集，再修改排序；
- 分别测量知识检索、符号检索、图扩展；
- 引入 task type、feature/domain、exact symbol、path proximity；
- 报告失败样本，不只报告平均值。

### 6.7 非 Git 项目支持不明确

真实目标 `11.0.0.0` 是 SVN 工作副本。当前系统将 Git 作为强基线，但非 Git 时只退化为空 commit。

要求：

- 明确 MVP 是否支持 SVN；
- 若支持，定义 RevisionProvider 抽象和 SVN Provider；
- 若不支持，init 必须提示“file-hash-only mode”；
- 不得把缺少 Git 误报为 clean Git 仓库。

## 7. 目标产品架构

### 7.1 知识层次

目标系统采用五层知识：

1. **Evidence**：文件、符号、关系、路由、服务、配置和 Schema；
2. **Generated**：模块地图、服务拓扑、协议地图和数据模型；
3. **Semantic Draft**：模型生成的 Feature Guide、Workflow 和 Recipe；
4. **Curated**：经过人工或受控策略确认的语义知识；
5. **Decision/History**：ADR、ChangeSet、Proposal 和功能演进记录。

### 7.2 首次语义建库流程

```text
范围和隐私预检
-> 确定性结构索引
-> 项目和框架识别
-> 功能域候选聚类
-> 为每个功能域组装有上限的 EvidencePack
-> 模型生成 FeatureGuideDraft
-> 来源、符号、路径和 Secret 校验
-> Proposal
-> 人工/策略审核
-> Curated Feature Guide
-> 真实问题评测
```

### 7.3 后续功能开发流程

```text
用户任务
-> knowledge_context
-> FeatureGuide + Workflow + Recipe
-> 实时代码锚点和影响范围
-> 实现和验证
-> ChangeSet（含测试）
-> 受影响知识计算
-> 语义更新 Proposal
-> 审核并更新知识
```

### 7.4 模型 Provider 边界

模型能力必须通过 `ModelProvider` 抽象：

```text
ModelProvider
├── provider_id
├── model_id
├── capabilities
├── preview_payload
├── generate_structured
├── usage
└── health
```

必须支持：

- disabled；
- 本地/内网 Provider；
- 显式启用的云 Provider；
- 可测试 Fake Provider；
- 请求字段预览；
- Secret 和路径策略；
- 模型、提示词和 Schema 版本记录；
- 超时、重试、取消和检查点恢复。

### 7.5 Feature Guide 最小 Schema

```yaml
id: feature.player.bag.use-item
title: 背包物品使用
domain: player
status: draft|verified|stale|conflicted
summary: ...
responsibilities: []
entrypoints: []
workflow_steps: []
services: []
data_and_state: []
invariants: []
extension_points: []
change_recipe: []
configuration: []
tests: []
pitfalls: []
analogous_features: []
unknowns: []
sources: []
confidence: inferred|verified
generation:
  provider: ...
  model: ...
  prompt_version: ...
  schema_version: ...
```

任何没有来源的结论必须进入 `unknowns` 或明确标记 inferred。

## 8. 实施工作包

### WP-00：基线可靠性和误导行为修复

**优先级：P0**  
**依赖：无**  
**实施状态：已完成（0.1.2，2026-08-06）**

任务：

1. 修复 codegraph 配置占位行为；
2. 区分 commit alignment 和 content freshness；
3. 修复空 curated 模板可信度；
4. 为模块页增加截断声明；
5. 对未接线配置发出 doctor/check 警告；
6. 增加运行时 Schema 验证；
7. 增加报告中的已知失败回归测试。

建议文件：

- `src/project_knowledge/config.py`
- `src/project_knowledge/engine.py`
- `src/project_knowledge/knowledge.py`
- `src/project_knowledge/service.py`
- `src/project_knowledge/schemas.py`
- `tests/test_config.py`
- `tests/test_integration.py`

验收：

- 选择未安装的 codegraph 明确失败；
- HEAD 变化后 status 能区分已校验和未校验；
- 空模板不作为 verified 项目意图；
- 大模块页面明确报告截断；
- 所有配置字段要么有行为，要么明确 unsupported；
- 现有测试和新增测试全部通过。

验收证据：

| 验收项 | 0.1.2 实现证据 | 自动化证据 |
| --- | --- | --- |
| codegraph 不再静默回退 | `create_engine` 对尚无真实 Adapter 的 `engine: codegraph` 抛出带修复建议的明确错误 | `test_unavailable_codegraph_engine_fails_explicitly` |
| 提交对齐与内容新鲜度分离 | `status` 独立返回 `content_fresh`、`commit_aligned` 和 `commit_alignment`；无内容变化的 `sync` 可刷新提交元数据并返回 `commit_reconciled` | `test_commit_alignment_is_distinct_from_content_freshness` |
| 空模板不冒充人工意图 | 新模板含 `project-kb:template` 标记，删除标记前记录为 `inferred` 且无 `last_verified_at` | `test_template_is_inferred_until_human_content_replaces_marker` |
| 大模块不静默截断 | 模块页分别计算符号和关系总数，超过 300/150 时以中文声明展示上限和后续查询入口 | `test_large_module_reports_symbol_and_relation_truncation` |
| 未接线配置透明可见 | `ProjectConfig.capability_warnings` 将提案、触发器、非默认 generated mode、embeddings、外发和遥测状态暴露给 `doctor` 与 `check/status` | `test_capability_warnings_name_every_unwired_setting`、`test_doctor_and_check_report_unwired_configuration` |
| 运行时 Schema 验证 | 无第三方依赖的运行时验证器覆盖本项目使用的 JSON Schema 子集；KnowledgeRecord 清单和 ChangeSet 事件落盘前强制验证 | `test_required_empty_collections_are_preserved_and_validate`、`test_runtime_validator_rejects_invalid_record` |
| 回归测试全绿 | 本批先观察 7 个新增覆盖点失败，再完成实现 | `PYTHONPATH=src python3 -m unittest discover -s tests -v`：20/20 通过 |

剩余说明：WP-00 只负责让尚未实现的能力明确失败或告警；真实 CodeGraph Adapter、Proposal 审核链路、向量检索、模型 Provider、配置迁移和遥测策略分别由后续工作包交付，不能因本工作包完成而视为这些能力已完成。

### WP-01：代码图引擎契约与真实 Adapter

**实施状态：0.1.8 已完成本地 Builtin 公共契约、能力报告和正式 CodeGraph 替代决策；真实外部 CodeGraph Adapter 仍未接入。**

**优先级：P0**  
**依赖：WP-00**

任务：

1. 扩展 `CodeIndexEngine`：
   - initialize；
   - sync；
   - search_symbols；
   - get_source；
   - trace；
   - impact；
   - affected_tests；
   - status；
2. 保持 SQLite 私有 Schema 不暴露；
3. 实现 Builtin Adapter；
4. 实现真实 CodeGraph Adapter 或形成正式替代决策；
5. capability negotiation；
6. 引擎版本、语言精度和失败降级报告。

0.1.8 验收证据：CodeIndexEngine 与 BuiltinCodeIndexEngine 暴露统一查询方法；测试只调用公共契约；status 报告适配器版本、语言精度和限制；create_engine 选择 codegraph 时明确失败；ADR-0002 已通过 Proposal 审核应用。

验收：

- builtin 和 codegraph 具有同一公共契约；
- Adapter 测试不依赖私有数据库 Schema；
- trace 和 impact 有确定性结果及 confidence；
- codegraph 不可用时行为清晰；
- doctor 能报告真实能力。

### WP-02：Lua/Skynet Evidence Adapter

**实施状态：0.1.8 已完成 Lua/Skynet 离线证据解析、服务/调用/派发关系、配置/SQL 解析、模块边界和 2,887 文件只读镜像验证；动态运行时语义和业务精度仍需 D-007。**

**优先级：P0**  
**依赖：WP-01**

目标样本：

`C:\Users\mei\Desktop\11.0.0.0`

已知规模：

- 业务范围约 2783 个可索引文件；
- 约 502203 行；
- `dev` 约 2755 文件、499860 行；
- 约 12112 处 require；
- 约 31321 个函数；
- 5 个启动入口；
- 约 33 个服务创建调用。

任务：

1. 支持裸 `require "x"` 和括号形式；
2. 提取 `function module.name`、`function obj:name`；
3. 识别项目 class/base 模式；
4. 识别 `skynetx.start`；
5. 识别 newservice、uniqueservice、name；
6. 识别 call、send、cluster proxy/call/send；
7. 识别 protocol/run/exec 派发；
8. 支持 `.conf`；
9. 提取 SQL Schema；
10. 按 process/domain/service 分层模块；
11. 为 Skynet 框架和业务代码设置不同所有权/排序；
12. 增加 SVN 或 file-hash-only revision 模式；
13. 大模块分片，不静默截断。

0.1.8 验收证据：单元测试覆盖裸/括号 require、module.name、obj:run、服务创建/命名、skynet/cluster call/send、dispatch、.conf 与 SQL；只读镜像得到 2,887 文件、35,210 符号、168,141 关系、99.97% 解析成功，源目录未写入。未完成项是运行时语义、SVN revision、随机 30 条业务边精度和 D-007 标准答案。

验收：

- 代表性 require、服务和协议样例均有单元测试；
- 五个 main 入口被识别；
- 业务目录不会被压成单一 dev 模块；
- 随机抽样至少 30 条边，报告 precision；
- 初始化不索引 .svn、日志、dump 和二进制；
- 目标项目 dry-run 给出范围和风险报告；
- 未经授权不向目标项目写入。

### WP-03：Model Provider、隐私和 EvidencePack

**优先级：P0**  
**依赖：WP-00，可与 WP-01/02 部分并行**
**实施状态：0.1.4 已完成工作包验收；生产云模型选择和外发范围仍由 D-001/D-002 控制，不阻塞 WP-04 Fake Provider 闭环**

任务：

1. 定义 ModelProvider；
2. 定义 ProviderConfig 和模型能力；
3. 实现 disabled 和 Fake Provider；
4. 至少实现一种经用户授权的本地或云 Provider；
5. 定义 EvidencePack Schema；
6. 按 Token 和文件数量限制证据包；
7. Secret 检测和脱敏；
8. `--dry-run` 预览将发送内容；
9. 记录 provider/model/prompt/schema 版本；
10. 超时、重试、取消、缓存和 checkpoint；
11. 默认 local_only 下禁止外发。

验收：

- 默认配置不会发生网络请求；
- Fake Provider 可完成端到端测试；
- 云 Provider 未显式授权时拒绝；
- dry-run 可列出字段、文件和脱敏结果；
- 测试中不会暴露 Secret；
- 相同 EvidencePack 具有稳定哈希。

0.1.4 验收证据：

- `src/project_knowledge/evidence.py` 与 `EVIDENCE_PACK_SCHEMA` 实现相对路径边界、文件/Token 上限、高风险路径排除、Secret 脱敏和稳定哈希；
- `src/project_knowledge/provider.py` 实现 disabled、Fake、HTTP JSON、能力声明、显式授权、超时、重试、取消、缓存和检查点；
- `project-kb generate --dry-run` 只输出字段、相对文件、Token、排除项、脱敏统计和策略问题，使用不可执行 PreviewProvider；
- loopback HTTP 使用标准库真实往返测试；未授权云端在 transport 前拒绝；非法模型输出不缓存；缓存命中重新验证 Schema；
- `tests/test_provider.py`、`tests/test_schemas.py`、`tests/test_config.py` 覆盖正负路径，25 题质量门新增五条 WP-03 问题并通过绝对阈值。

### WP-04：Feature Guide、Workflow 和 Recipe

**优先级：P0**  
**依赖：WP-03，受益于 WP-02**
**实施状态：0.1.5 已完成工作包的生成到 draft 最小闭环；0.1.6 已由 WP-05 接通受控审核提升，真实 Lua/Skynet 业务验证仍依赖 WP-02/D-007**

任务：

1. 定义 FeatureGuide/Workflow/Recipe Schema；
2. 项目功能域候选生成；
3. 证据包到语义草案的结构化生成；
4. 来源存在性验证；
5. 符号和路径引用验证；
6. inferred/unknowns 管理；
7. generated、draft、verified 生命周期；
8. Feature Guide 分片和索引；
9. 中文默认模板；
10. 支持已有文档作为候选证据，而不是默认权威来源。

验收：

- 可为“背包物品使用”等样例生成完整草案；
- 每个确定性陈述有来源；
- 无来源结论进入 unknowns；
- Feature Guide 可被 search/get/context 检索；
- 来源变化后正确标记过期；
- 模型输出不符合 Schema 时不落库。

0.1.5 验收证据：

- `FEATURE_GUIDE_DRAFT_SCHEMA`、`WORKFLOW_SCHEMA` 和 `RECIPE_SCHEMA` 要求完整职责、入口、工作流、依赖、数据、不变量、扩展点、开发/验证/回滚步骤、测试、陷阱和 unknowns；确定性陈述的 `sources` 至少一项，模型不能输出 `verified`；
- `SemanticKnowledgeService` 通过 Fake Provider 完成“背包物品使用”端到端样例，生成默认中文 Markdown 与结构化 JSON，并按 `feature_id` 独立分片；
- `FeatureGuideValidator` 在缓存和知识写入前验证 EvidencePack 成员、项目边界、文件存在、行号、文件哈希、符号 ID/路径/定义范围/哈希；已有文档只能标记 `candidate`；
- 非法 Schema、无来源陈述、无效符号或错误文档权威均不产生草案或 Provider 缓存；Workflow 顺序必须从 1 连续递增；
- KnowledgeRecord 增加 `draft` ownership；KnowledgeGenerator、重建保留、待同步检测、Manifest、索引、FTS 和 search/get/context 已接入草案；draft 始终要求实时源码，来源变化后变为 `potentially_stale`；
- `feature-candidates` 从确定性模块/符号索引返回带来源候选；Feature Guide 获得类型权重和中文标题包含匹配；
- `tests/test_semantic.py` 覆盖完整样例、Schema 负例、零落库、候选发现、三种检索入口和过期传播；全量 49 项回归及新增后的针对性测试通过；
- 快速质量门扩展到 30 题，在不降低 0.1.4 阈值的前提下通过所有可用策略绝对门；codegraph 仍明确不可用。

保留边界：系统能确认引用存在、属于证据包且未被替换，不能纯静态证明任意自然语言陈述都由引用语义蕴含。草案因此保持 `draft/generated`；0.1.6 可通过 WP-05 显式审核进入 curated generated block，但审核人仍必须判断引用是否真正支持语义结论。

### WP-05：Proposal 审核闭环

**优先级：P0**  
**依赖：WP-04**
**实施状态：0.1.6 已完成本地单项目审核基础闭环；生产模型自动建议、Git PR 多人审核和批量队列治理留待后续工作包**

任务：

1. Semantic Update Queue；
2. Proposal 存储和稳定 ID；
3. Patch operation Schema；
4. `project-kb propose [range]`；
5. `project-kb apply <id>`；
6. `project-kb reject <id>`；
7. 审核人、时间、理由和来源记录；
8. generated block 支持；
9. ADR 只生成追加草案；
10. 删除和 supersedes 证据；
11. 幂等、冲突和过期提案处理。

验收：

- 未审核提案不改 curated；
- apply 只修改目标和 generated block；
- reject 保留审计记录；
- 过期提案不能直接应用；
- ADR 不静默改写；
- CLI 全部支持 dry-run/json/quiet；
- 完整集成测试覆盖生成、应用、拒绝和冲突。

0.1.6 验收证据：

- `PatchOperation` 与 `PROPOSAL_SCHEMA` 定义 `upsert_generated_block`、`delete_generated_block` 和 `append_adr_draft`，运行时拒绝字符串 operation、非法路径、空理由/来源、越界置信度和不完整删除证据；
- `ProposalService.create` 根据目标哈希、规范化来源及其本地哈希、理由、operation、置信度和 change range 生成 `kp-<16 hex>` 稳定 ID；重复输入返回同一审计记录，pending 阶段不修改目标；
- `ProjectService.sync` 将 ChangeSet 写入稳定 Semantic Update Queue 项；创建与 changed_files 或 range 匹配的 Proposal 后，队列项保留 `proposal_created` 状态和关联 ID；
- `create_from_feature_draft` 再次校验 WP-04 结构化草案，收集草案 Markdown/JSON 与逐陈述源码路径的哈希，并生成按功能分片的 curated generated block 提案；
- apply 前重新计算目标和全部可解析来源哈希；任何变化都会原子保存 `conflicted`、审核尝试和原因，旧提案不能继续应用；
- apply 只对单一目标中的命名 generated block 做精确 upsert/delete；人工段落保持不变，dry-run 返回统一 diff，重复 apply 为幂等；
- reject 不改目标且保留 reviewer、reviewed_at、review_reason；applied/rejected/conflicted 状态不能被越权转换；
- 删除 generated block 必须同时提供 `deleted_sources` 和 `supersedes`；ADR 只允许在不存在的 decisions 路径创建强制“状态：草案”的新文件，任何已有 ADR 目标都会在提案创建阶段拒绝；
- `project-kb propose [range]`、`apply <id>`、`reject <id>` 均支持 `--dry-run`、`--json` 和 `--quiet`；中文操作说明见 `docs/proposal-guide.md`；
- `tests/test_proposal.py` 与 `tests/test_semantic.py` 覆盖稳定 ID、零目标写入、dry-run、应用、拒绝、目标/来源冲突、删除、ADR、幂等、CLI 和 Feature Guide 端到端提升；0.1.6 升版前全量 57 项测试通过；
- 快速质量门扩展到 35 题，新增 Proposal 稳定 ID、应用冲突、草案提升、删除/ADR 和 Semantic Update Queue 五类问题；未降低 0.1.5 的冻结指标阈值，所有可用策略绝对门通过，codegraph 继续明确报告不可用。

保留边界：

- Semantic Update Queue 当前是本地 JSON 审计队列，没有后台模型 worker、重试调度、批量确认或 Git PR 审核；
- Proposal 的 applied 状态表示指定审核人已接受生成区块，不等于系统自动证明自然语言语义正确；
- supersedes 已记录在 Patch operation 和 ADR 草案中，尚未自动改写对应 KnowledgeRecord 的完整替代图；
- 文件写入采用单项目锁和原子替换，但目标文件与 Proposal 状态不是跨文件系统事务；进程在两次原子替换之间崩溃的恢复属于 WP-07。

### WP-06：面向功能开发的检索与影响分析

**优先级：P0**  
**依赖：WP-04；完整闭环依赖 WP-05**
**实施状态：0.1.7 已完成本地可用 MVP；Lua/Skynet 动态运行时和真实业务负责人确认仍留在 WP-02/D-007**

任务：

1. 任务类型识别；
2. feature/domain/module 检索；
3. exact symbol/path 权重；
4. Feature Guide 优先；
5. Workflow/Recipe/ADR 联合检索；
6. 多跳图扩展和预算；
7. 影响模块、测试、配置、知识和数据迁移；
8. 参考实现和 analogous feature；
9. unknowns 和实时源码读取建议；
10. 检索解释和得分分解；
11. 可选向量检索接口。

验收：

- “新增类似功能”问题返回扩展点、修改建议和验证；
- precision/recall 在扩展评测集达到门槛；
- context 不超过预算；
- stale/inferred 不作为单一依据；
- 不返回宽泛模块的全部来源文件；
- 每个结果能解释为何被选中。

0.1.7 验收证据：

- `KnowledgeAPI.classify_task` 对 new_feature、bug_fix、refactor、impact_analysis 和 investigation 返回中文 signals、置信度与可读 rationale；
- `KnowledgeAPI.search` 返回 `text_match`、`score_breakdown`、`why_selected`，明确披露文本、可信度、新鲜度、Feature Guide 与模块权重；
- `KnowledgeAPI.context` 返回 `task_type`、`likely_modules`、`reference_implementations`、`extension_points`、`retrieval_explanation`、`unknowns` 和验证命令，并在预算收缩时优先保留来源正文；
- `KnowledgeAPI.impact` 支持 `max_hops`/`max_relations` 有界 BFS，返回 `relation_hops`、`impact_explanation`、受影响模块/测试/知识；新增功能和影响分析默认最多两跳；
- `tests/test_retrieval_wp06.py` 覆盖分类、评分拆解、参考实现、扩展点和有界多跳；全量 59 项回归通过；
- 快速质量门扩展为 40 条中文问题，0.1.7 基线 hybrid 文件/符号召回 `0.979167/0.854167`、成功率 `0.575`、平均 `1519.1` Token，不变量召回 `0.2`、设计原因召回 `1.0`，所有绝对门通过；
- 仍明确不把动态派发、反射、运行时依赖注入和未经负责人确认的 Lua/Skynet 业务结论包装为静态事实。

0.1.8 评估：主 40 题集保持冻结阈值；hybrid 文件/符号召回 0.970833/0.845833、成功率 0.575、平均 1349.075 Token，generated 来源覆盖 0.9。only-Markdown 文件/不变量召回及其回归门暂未通过，未降低阈值；5 题 WP-01/WP-02 补充集独立保存，不纳入主门槛。

### WP-07：Git、watcher、daemon 和并发

**实施状态：0.1.9 已完成单项目 watcher 协调、保存竞态二次哈希、分支补偿、PID/崩溃健康、结构化日志和受控 Git hooks；共享后台 daemon、真正事件驱动防抖、多客户端服务和完整并发状态机仍未完成。**

**优先级：P1**  
**依赖：WP-00/01**

任务：

1. 共享后台 daemon；
2. 单项目单协调者；
3. 多 MCP 客户端连接；
4. 文件事件防抖和合并；
5. 解析后二次哈希；
6. 变更期间重新排队；
7. post-checkout/post-merge/pre-commit 或连接补偿；
8. worktree 独立状态；
9. crash recovery 和 PID 检测；
10. 结构化日志；
11. daemon/status/doctor 健康。

验收：

- 多客户端不会启动重复 watcher；
- 保存竞态不会提交错误 hash/content 组合；
- 分支切换不会返回旧分支事实；
- 崩溃后可恢复；
- 写入时读取上一份有效快照；
- 相关并发测试稳定通过。

0.1.9 验收证据：

- tests/test_watch_wp07.py 覆盖单 watcher 拒绝重复、死进程恢复、解析期间二次保存、分支切换补偿、watcher lifecycle 日志和 hooks 标记；全量回归 66 项通过；
- status/doctor 报告 watcher_health、PID、heartbeat、coordinator、branch_aligned 与 commit_aligned；
- 评测集 evaluation/questions-wp07.jsonl 和报告 evaluation/reports/wp07-0.1.9.json 已生成；当前独立评测仅作为能力定位，不纳入主质量阈值；
- 尚未完成：共享后台 daemon、跨 MCP 客户端复用单一 watcher、真正文件事件合并/防抖、worktree 状态隔离的专门并发测试和 crash recovery 状态机。

### WP-08：配置迁移、客户端和发布

**优先级：P1**  
**依赖：核心配置稳定后**  
**实施状态：0.1.10 已完成配置 Schema/v0→v1 迁移、Claude/Cursor/Gemini 基础适配、所有权标记测试、核心/插件版本同步和 wheel/sdist 构建验证；Windows 原生生命周期 CI、客户端真实版本矩阵和正式发布流水线仍未完成。**

任务：

1. 配置 JSON Schema；
2. `project-kb migrate`；
3. 向前兼容策略；
4. Claude/Cursor/Gemini 适配器；
5. Plugin 安装、更新和卸载测试；
6. Windows/WSL/原生路径兼容；
7. 包构建和发布验证；
8. Provider 与 Plugin 版本兼容矩阵。

验收：

- 旧配置可 dry-run 迁移；
- 用户自有配置不被覆盖；
- 各适配器只写工具拥有的标记区块；
- 卸载保留知识；
- Windows 与 WSL 路径有测试。

0.1.10 验收证据：

- `CONFIG_SCHEMA` 作为 `config-v1.json` 随初始化发布；Schema 允许用户扩展字段，版本高于 1 时旧程序显式失败；
- `project-kb migrate --dry-run` 和正式迁移覆盖 v0→v1；JSON 未知字段与 YAML 非版本文本保持不变；
- `install/uninstall --client claude|cursor|gemini` 只操作对应所有权标记，重复安装保持单一区块，卸载保留用户正文和知识库；
- `tests/test_wp08.py` 与 `tests/test_versioning.py` 覆盖配置迁移、Schema 发布、Windows 绝对路径拒绝、WSL/原生相对路径、插件/核心版本一致和 dry-run 不写入；0.1.10 全量回归 71 项通过；
- 独立评测 `evaluation/questions-wp08.jsonl` / `evaluation/reports/wp08-0.1.10.json`：hybrid 与 code 文件召回均为 1.0，符号召回均为 0.541667，不变量召回为 0、成功率为 0.25；未启用质量门，结果作为 WP09 排序和不变量检索缺口，不下调现有冻结阈值；
- `docs/compatibility-matrix.md` 记录 Python/OS、Provider、客户端、配置和发布边界，默认中文；
- 临时目录成功构建 `project_knowledge_cli-0.1.10-py3-none-any.whl` 与 `project_knowledge_cli-0.1.10.tar.gz`；wheel 元数据为 Version 0.1.10、Requires-Python >=3.11，sdist 包含 WP08 关键源码；
- 构建警告：pyproject 旧式 license 表和 license classifier 已被 setuptools 弃用，需在正式发布包中迁移到 SPDX；当前警告不阻断 0.1.10 制品生成；
- 尚未完成：Windows 原生 watcher/hooks/锁实机 CI、Claude/Cursor/Gemini 不同版本的端到端启动测试、插件更新/回滚的真实客户端测试、签名上传和正式发布流水线。

### WP-09：真实评测、性能和产品验收

**优先级：P0，贯穿全部工作包**  
**依赖：每个工作包都必须更新评测**  
**实施状态：0.1.3 已建立首个可重复基线；工作包持续进行，不得标记为整体完成**

任务：

1. 先建立 20～50 个真实功能问题；
2. 使用至少一个 1000+ 文件项目；
3. 建立 grep + Read 基线；
4. 建立 only-codegraph 基线；
5. 建立 only-Markdown 基线；
6. 测量文件、符号、调用路径和扩展点；
7. 测量不变量和设计原因召回；
8. 测量过期检测；
9. 测量 Token、工具调用次数和最终成功率；
10. 建立 500/5000 文件性能 harness；
11. 固定硬件、仓库版本和失败样本；
12. CI 运行快速集，定期运行完整集。

首批真实问题至少包括：

- 玩家登录从哪里进入；
- 背包使用物品经过哪些模块；
- 玩家数据如何持久化；
- 新增一种礼包应该复用哪个实现；
- 新增 Skynet 服务需要修改哪些配置；
- 某协议如何派发到业务方法；
- 某资源变更需要保持哪些不变量；
- 某服务修改会影响哪些测试和知识。

验收门槛需要在基线建立后冻结。冻结前不得用未经验证的平均值宣传效果。

0.1.3/0.1.4/0.1.5 基线证据：

| 范围 | 证据 | 结论 |
| --- | --- | --- |
| 30 条快速问题 | `evaluation/questions.jsonl` | 在原 25 题上增加 Feature Guide Schema、语义生成、来源校验、草案生命周期和功能检索；答案由当前仓库实时源码校验 |
| 20 条 Lua/Skynet 业务候选 | `evaluation/lua-skynet-ground-truth-candidates.md` | 问题已建立，但标准答案、负责人和确认日期等待 D-007，不能进入冻结阈值 |
| 多策略对照 | `src/project_knowledge/evaluate.py` | hybrid、grep + Read、only-code、only-Markdown 可重复运行；only-codegraph 明确为 `adapter_unavailable`，等待 WP-01/D-005 |
| 指标与质量门 | `evaluation/thresholds.json`、`tests/test_evaluate.py` | 按适用样本计算文件/符号/调用路径/扩展点/不变量/设计原因，记录 Token、工具调用、延迟和成功率；支持按策略阈值及基线退化退出码 2 |
| 真实 1000+ 文件项目 | `evaluation/reports/lua-skynet-readonly-mirror.json` | 只读发现 2881 文件并在临时镜像完成初始化；35043 符号、160537 关系、1 个解析错误、成功率 0.9997；源目录全树快照前后一致 |
| 真实项目性能 | 同上 | 初始化约 128.6 秒；通用上下文探针约 70.2 秒。证明“可完成”，不证明“可流畅使用” |
| 500/5000 性能 | `evaluation/reports/performance-0.1.3.json` | 5000 文件初始化约 93.1 秒，status P95 约 37.5 秒，context P95 约 141.1 秒，noop sync P95 约 38.8 秒；两档过期屏蔽探针通过 |
| CI | `.github/workflows/quality.yml` | push/PR 运行快速集；每周运行 500/5000 完整性能集并上传报告 |
| 真实失败回归 | `tests/test_engine.py` | Lua/Python 同文件重复定义曾触发 SQLite 唯一约束崩溃；现以首个稳定 ID、后续 `@line` 消歧并有正向回归测试 |
| 自污染回归 | `src/project_knowledge/config.py`、`tests/test_config.py`、`tests/test_engine.py` | 报告和基线目录从发现与索引中排除；稳定轮次索引文件从 38 降为 36 |

0.1.3 稳定快速基线：hybrid 文件/符号召回 `0.958333/0.833333`、成功率 `0.45`；grep + Read 文件召回/精确率 `0.675/0.29375`；only-Markdown 文件召回/精确率 `0.933333/0.087313`；generated 来源覆盖 `0.9`。首次冻结门槛根据排除自污染后的稳定轮次设置，并只保留小幅跨环境余量；冻结后的回归比较以 `evaluation/baselines/self-repo-0.1.3.json` 为准，本轮绝对阈值与相对回归门均通过。

0.1.4 稳定快速基线：25 题绝对阈值及同数据集相对回归门均通过；hybrid 文件/符号召回 `0.966667/0.813333`、成功率 `0.52`；grep + Read 文件召回/精确率 `0.726667/0.305`；only-Markdown 文件召回/精确率 `0.933333/0.098372`；生成来源覆盖 `0.9`。冻结基线为 `evaluation/baselines/self-repo-0.1.4.json`。

0.1.5 稳定快速基线：30 题绝对阈值及同数据集相对回归门均通过；hybrid 文件/符号召回 `0.972222/0.844444`、成功率 `0.6`、平均 `1507.5` Token；only-code 文件/符号召回 `0.944444/0.844444`、平均 `190.166667` Token；grep + Read 文件召回/精确率 `0.719444/0.316666`；only-Markdown 文件召回/精确率 `0.922222/0.107086`；生成来源覆盖 `0.9`。冻结基线为 `evaluation/baselines/self-repo-0.1.5.json`。

0.1.6 稳定快速基线：35 题绝对阈值及同数据集相对回归门均通过；hybrid 文件/符号召回 `0.976190/0.842857`、成功率 `0.542857`、平均 `1586.628571` Token；only-code 文件/符号召回 `0.942857/0.842857`、成功率 `0.514286`、平均 `210.514286` Token；grep + Read 文件召回/精确率 `0.735714/0.316326`、平均 `6251.028571` Token；only-Markdown 文件召回/精确率 `0.900000/0.094825`、不变量召回 `0.4`、平均 `3455.285714` Token；生成来源覆盖 `0.9`。冻结基线为 `evaluation/baselines/self-repo-0.1.6.json`。codegraph 明确返回 `adapter_unavailable`，未伪造结果。

0.1.7 稳定快速基线：40 题绝对阈值及同数据集相对回归门均通过；hybrid 文件/符号召回 `0.979167/0.854167`、成功率 `0.575`、平均 `1519.1` Token；only-code 与 grep/Read 继续满足冻结门槛；hybrid 不变量召回 `0.2`、设计原因召回 `1.0`，生成来源覆盖 `0.9`。WP-06 新增任务分类、评分拆解、有界多跳、参考实现和 unknowns 问题；冻结基线为 `evaluation/baselines/self-repo-0.1.7.json`。codegraph 明确返回 `adapter_unavailable`，未伪造结果。

0.1.14 WP-09 复核证据：

- 针对 0.1.13 主门禁暴露的 Markdown 版本知识缺口，通过受控 Proposal 更新 docs/knowledge/curated/conventions.md：补充 src/project_knowledge/__init__.py 与 CHANGELOG.md 来源锚点，并写明“同一批修改或新增内容只递增一次补丁版本”不变量；未降低阈值、未放宽策略页数或 Token 限制。
- evaluation/reports/wp09-0.1.14-main.json 全策略门禁通过：Markdown file_recall 0.900000、invariant_recall 0.5；hybrid file_recall 0.9875、symbol_recall 0.845833、invariant_recall 0.6；CodeGraph 仍为允许的 adapter_unavailable 警告。
- evaluation/reports/wp08-0.1.14.json 回归通过：hybrid 文件/符号/不变量召回均为 1.0，成功率 1.0；only-code 文件/符号召回均为 1.0，不变量召回 0.0，继续作为静态代码边界证据。
- 0.1.14 全量测试通过（73 项）；wheel 与 sdist 均构建成功，wheel 元数据 Version 0.1.14、Requires-Python >=3.11。构建仍报告 setuptools 旧式 license 表/classifier 弃用警告，不阻断制品生成。

0.1.13 WP-09 复核证据：

- `evaluation/reports/wp08-0.1.13.json` 使用与 0.1.10 相同的四题数据集：hybrid 文件/符号/不变量召回均为 1.0，成功率 1.0，平均上下文约 1650 Token；only-code 符号召回 1.0 但不变量召回 0.0，明确静态代码策略边界；
- 0.1.13 新增显式中文短语到英文标识符组合、结构化标识符优先、长知识相关片段截取和安全约束行优先级；`tests/test_retrieval_wp06.py` 覆盖中文任务、长文档末尾不变量和 Token 上限；
- `evaluation/reports/wp09-0.1.13-main.json` 的完整冻结质量门明确暴露既有 markdown 策略缺口：file_recall 0.883333 低于 0.9 下限，invariant_recall 0.4 相对 0.1.7 基线 0.5 回退；codegraph 仅报告 adapter_unavailable。未降低阈值，下一批优先处理 Markdown 精度/不变量召回和真实 CodeGraph Adapter；
- 当前结论：WP09 的中文 hybrid 功能开发指导路径已完成基础闭环，但整体产品质量门仍未完成，不能宣称 MVP 全部达标。
- 0.1.13 制品复验：临时目录成功生成 `project_knowledge_cli-0.1.13-py3-none-any.whl` 与 `project_knowledge_cli-0.1.13.tar.gz`；wheel 元数据 Version 0.1.13、Requires-Python >=3.11。

冻结限制：

- `evaluation/thresholds.json` 冻结 0.1.7 的 40 题实测能力下限，不代表产品目标；
- codegraph 不可用、generated 来源覆盖不足 100%、大项目查询延迟过高、真实业务答案未确认和端到端开发成功率缺失，均保持为公开未完成项；
- 后续每个工作包必须更新问题、基线和失败样本，但不得为让 CI 通过而降低阈值；阈值变化必须写明证据和原因。

## 9. 工作包依赖和推荐顺序

```text
WP-00 基线可靠性
├── WP-01 引擎契约
│   ├── WP-02 Lua/Skynet
│   └── WP-07 生命周期与并发
├── WP-03 Provider/EvidencePack
│   └── WP-04 Feature Guide
│       ├── WP-05 Proposal
│       └── WP-06 功能检索
├── WP-08 配置与客户端
└── WP-09 评测贯穿所有阶段
```

推荐实施顺序：

1. WP-00；
2. WP-09 的评测基线；
3. WP-03 的 Provider 抽象和 Fake Provider；
4. WP-04 的 Schema 和最小端到端语义草案；
5. WP-05 的 Proposal 审核闭环（0.1.6 已完成本地基础版；0.1.7 WP-06 已完成检索 MVP）；
6. WP-06 的功能检索与影响分析；
7. WP-01 和 WP-02 提升真实项目证据质量，并在 Lua/Skynet 目标项目上复核；
8. WP-07、WP-08 完善团队级可靠性和交付。

这样安排的原因：

- 用户价值是功能开发指导，必须尽早建立最小语义闭环；
- 不能先投入完整 Lua 解析器后才验证 Feature Guide 是否有用；
- Fake Provider 可以在不决定云模型的前提下完成架构和测试；
- 真实评测必须先于排序优化；
- Proposal 在生成质量可测量后再开放 apply。

## 10. 版本和变更规则

1. 本报告建立时版本从 0.1.0 递增到 0.1.1；
2. 后续每批实现只递增一次补丁版本；
3. 同一批实现触发的生成知识同步不重复递增；
4. 每个版本在 CHANGELOG 中对应工作包和验收结果；
5. 未通过当前工作包验收不得标记工作包完成；
6. 仅新增设计草案也属于项目变更，需要版本递增；
7. 自动生成文件变化不单独递增。

## 11. 每批开发的强制流程

1. 调用 `knowledge_status`；
2. 调用 `knowledge_context`；
3. 跨模块前调用 `knowledge_impact`；
4. 指明本批对应的 WP 和需求 ID；
5. 只读取相关源码；
6. 先补失败测试或评测样本；
7. 实现最小闭环；
8. 运行单元、集成和相关评测；
9. 运行版本递增工具；
10. 同步知识库；
11. 复核 curated/ADR 新鲜度；
12. 更新本报告中的状态和证据；
13. 最终报告版本、测试、评测和剩余缺口。

## 12. 完整测试矩阵

### 12.1 单元测试

- 配置合法、非法、未知字段和迁移；
- Schema 验证；
- 文件、符号和任务来源；
- fresh/potentially_stale/stale/conflicted；
- generated block；
- EvidencePack 哈希和裁剪；
- Secret 检测；
- Provider 授权；
- Proposal operation；
- Feature Guide 验证；
- 任务分类和排序；
- Git/SVN revision provider。

### 12.2 集成测试

- 初始化、同步、删除、rename；
- 分支、worktree、detached HEAD；
- 文件在解析期间再次修改；
- 多 MCP 客户端；
- writer crash 和 stale lock；
- Provider 超时和恢复；
- Proposal 生成、应用、拒绝、冲突；
- ADR 追加；
- 卸载保留知识；
- Windows/WSL 路径。

### 12.3 端到端测试

- 小型 Python 项目；
- 小型 Lua/Skynet fixture；
- 1000+ 文件真实样本；
- 完全离线；
- Fake Provider；
- 授权 Provider；
- 功能开发问题到 Feature Guide；
- 完成任务后 ChangeSet 到 Proposal。

### 12.4 性能测试

| 指标 | 原始目标 |
| --- | --- |
| 500 文件首次初始化 | 30 秒内，不含可选 LLM |
| 5000 文件首次初始化 | 5 分钟内 |
| 单文件同步 | P95 < 2 秒 |
| knowledge_status | P95 < 200 ms |
| knowledge_search | P95 < 500 ms |
| knowledge_context | P95 < 2 秒 |
| 已知 pending 源的旧内容返回 | 0 |
| generated 来源覆盖率 | 100% |

所有性能结果必须附：

- CPU、内存、磁盘；
- 操作系统和文件系统；
- 仓库 commit/revision；
- 文件和代码行数；
- 冷/热缓存；
- P50/P95/P99；
- 失败和超时样本。

## 13. 安全和隐私验收

1. 默认不调用外部模型；
2. local_only 必须是强制策略而不是展示字段；
3. Provider 启用需要显式配置；
4. dry-run 显示将发送的文件、字段和估算 Token；
5. Secret、Token、密码和私钥默认脱敏；
6. 高风险路径可配置拒绝外发；
7. 请求和响应日志不能包含未脱敏源码；
8. Manifest 不含绝对路径；
9. 记录模型和提示词版本，但不记录 Secret；
10. 测试使用伪 Secret 并验证不泄漏。

## 14. 完成定义

### 14.1 单个需求完成

一个需求只有同时满足以下条件才算完成：

- 实现存在；
- 配置行为明确；
- 正向和负向测试存在；
- 相关评测样本存在；
- 文档和 Schema 更新；
- 来源和隐私要求满足；
- 知识库同步；
- 版本和 CHANGELOG 更新；
- 没有把已知限制隐藏在默认成功结果中。

### 14.2 工作包完成

- 工作包全部任务关闭；
- 所有验收条件通过；
- 没有 P0 未解决缺陷；
- 性能和质量没有相对基线退化；
- 审计矩阵状态和证据更新；
- curated 知识已审核或明确待审核。

### 14.3 产品 MVP 完成

除原始第 25 节要求外，还必须满足：

1. 至少一个真实 1000+ 文件项目完成初始化；
2. 至少 20 个功能问题有标准答案；
3. Feature Guide 可以指导至少三类真实功能修改；
4. 每个功能指导包含来源、扩展点、不变量和验证；
5. 任务完成后可生成受控 Proposal；
6. 与 grep + Read 相比有可复现改进；
7. 完全离线模式仍能使用已有知识和事实索引；
8. 云模型模式有授权、预览和脱敏；
9. 分支、并发和崩溃恢复测试通过；
10. 所有指标和失败样本可审计。

## 15. 决策门

以下决策不阻止先实现抽象和 Fake Provider，但在接入真实模型前必须确认：

| ID | 决策 | 需要确认 |
| --- | --- | --- |
| D-001 | 模型部署 | 云端、本地还是公司内网 |
| D-002 | 源码外发 | 哪些源码允许发送 |
| D-003 | 真实目标写入 | 是否允许在 11.0.0.0 原目录初始化 |
| D-004 | SVN 支持 | 正式支持还是 file-hash-only |
| D-005 | CodeGraph | 使用原设计候选还是其他引擎 |
| D-006 | Feature Guide 审核 | 全人工、风险分级还是策略自动接受 |
| D-007 | 评测答案 | 谁负责确认真实业务标准答案 |

## 16. 当前已知风险

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 语义知识幻觉 | 高 | EvidencePack、Schema、来源校验、Proposal |
| Lua 动态调用漏边 | 高 | 框架适配、低可信标记、运行时证据 |
| 大模块文档截断 | 高 | 分层、分片、任务检索 |
| 云模型泄漏源码 | 高 | 默认关闭、脱敏、预览、授权 |
| 检索高召回低精确 | 高 | 真实评测、任务分类、Feature Guide |
| 用户忽略提案 | 中 | 风险排序、CI 提醒、低摩擦审核 |
| Git/SVN 状态误判 | 中 | RevisionProvider、状态分离 |
| watcher 竞态 | 中 | 二次哈希和重新排队 |
| 知识库膨胀 | 中 | 生命周期、归档、分片、Token 预算 |
| Provider 成本或不可用 | 中 | 检查点、缓存、本地模式、降级 |

## 17. 审计后的首个实施里程碑

首个里程碑不是完整 Lua 解析器，而是：

> 使用 Fake Provider 在小型 fixture 上完成 EvidencePack → FeatureGuideDraft → 来源验证 → knowledge_context 的最小端到端闭环。

里程碑必须包含：

1. Feature Guide Schema；
2. EvidencePack Schema；
3. ModelProvider 接口；
4. Fake Provider；
5. 生成命令或服务入口；
6. 来源验证；
7. 草案知识存储；
8. context 优先返回 Feature Guide；
9. 至少五个功能开发评测问题；
10. 完整单元和集成测试。

完成该里程碑后，再用 Lua/Skynet Adapter 提升真实目标项目的证据质量。

0.1.4 已完成上述第 2、3、5 项以及生成命令的安全执行入口；WP-04 将完成 Feature Guide Schema、来源验证、草案存储、context 优先级和五类端到端功能开发样例。

## 18. 审计维护记录

| 日期 | 版本 | 说明 |
| --- | --- | --- |
| 2026-08-07 | 0.1.4 | 完成 WP-03：Provider/EvidencePack/Secret 脱敏/显式授权/缓存检查点；25 题绝对与相对质量门、44 项测试、编译和差异检查通过 |
| 2026-08-07 | 0.1.14 | 修复 WP-09 Markdown 版本知识来源锚点与中文不变量；全策略主门禁通过、WP08 回归与 73 项测试通过，wheel/sdist 构建成功；CodeGraph 适配器和真实业务验收仍未完成 |
| 2026-08-07 | 0.1.3 | 建立 WP-09 首个可重复基线：20 题、多策略质量门、真实项目只读镜像和 500/5000 性能报告；31 项测试、编译与差异检查通过；工作包持续进行 |
| 2026-08-06 | 0.1.2 | 完成 WP-00：提交对齐、配置告警、模板可信度、截断披露与运行时 Schema 校验 |
| 2026-08-06 | 0.1.1 | 建立首次完整需求对齐审计和后续实施基线 |


## 18.1 本阶段复核（WP-CG-01、WP-GUIDE-01）

本阶段按用户重新确认的最小目标验收：复用已安装的 CodeGraph，建立本地、可增量更新的项目级知识库，并生成类别级开发指导，而不是为登录、花园或公会某个具体功能写静态说明。

| 需求 ID | 验收内容 | 当前状态 | 证据 | 后续边界 |
| --- | --- | --- | --- | --- |
| CG-01 | 通过 CodeGraph 公开 CLI/API 完成初始化、同步、状态、文件、符号、调用关系、影响范围和测试查询 | 已完成 | `src/project_knowledge/codegraph.py`、`tests/test_codegraph.py` | 不读取私有数据库 |
| CG-02 | CodeGraph 是代码事实权威，本地 SQLite 仅作兼容缓存 | 已完成 | `CodeGraphEngine.parse` 实现说明 | 缓存不能替代指导证据 |
| CG-03 | gardenserver Lua/Skynet/zn 规则适配，事实带源码路径和行号 | 已完成（首版） | `src/project_knowledge/gardenserver.py`、`.project-kb/evidence/*.json` | 动态调用和隐式约定需人工确认 |
| GUIDE-01 | 第一层输出跨项目可迁移的方法论 | 已完成（中文基线） | `src/project_knowledge/guidance_templates.py`、`.project-kb/methodology/*.json` | LLM 重写/扩展是后续增强 |
| GUIDE-02 | 第二层输出当前项目适配，覆盖普通活动、普通玩家功能、登录模块 | 已完成（首版） | `.project-kb/guides/*.json`、`.project-kb/generated/*.md` | 真实开发任务评测后再提升为 verified |
| GUIDE-03 | 代码变化后 CodeGraph 与第二层指导同步 | 已完成（增量路径） | `ProjectService.sync/watch` 先同步 CodeGraph 再刷新指导；watch 回归测试 | 未对 gardenserver 业务源做破坏性修改演示 |
| DIR-01 | PKS 自有生成文件统一在 `.project-kb`，默认中文展示 | 已完成 | 单目录配置、中文文件名回归测试 | CodeGraph 1.5 自身要求可识别的 `.codegraph` 运行时目录，不能被 PKS 改名 |
| VER-01 | 每批变更只递增一次补丁版本并记录中文变更 | 已完成 | `src/project_knowledge/__init__.py`、`CHANGELOG.md` | 下一批变更继续递增一次 |

| 2026-08-11 | 0.1.16 | 完成 CodeGraph 公开 CLI 适配、gardenserver 规则证据和三类两层中文开发指导；PKS 自有产物统一 `.project-kb`，CodeGraph 上游 `.codegraph` 限制已记录；90 项测试通过 |

### 与原始需求的偏差处理

1. 原审计把 CodeGraph 和 Feature Guide 列为后续里程碑；按用户当前优先级，本阶段提前完成适配闭环。
2. 本阶段没有重写 CodeGraph，也没有读取私有数据库；事实查询全部走公开 CLI。
3. 第一层当前采用可审计的内置中文模板，保证离线和无模型时有可用基线；LLM 生成、评测和人工审核仍未宣称完成。
4. “单目录”已落实到 PKS 产物；CodeGraph 的 `.codegraph` 是上游固定运行时目录，强行搬入 `.project-kb` 会导致 Windows CodeGraph 报未初始化，因此保留并在配置中显式说明。

### 真实项目验收

gardenserver 的 CodeGraph 1.5.0 事实快照与业务文件指纹核验已完成，初始化批次可恢复，代码变化只生成第二层项目事实指导草稿。5 个业务类别的独立方法论与项目事实指导（共 10 份）已由用户完成审核并写入 KnowledgeStore；旧混合草稿未被复用。正式版本和 Markdown 投影均已生成。
# 0.1.31 当前交付校正：Git 生命周期与补偿

本节覆盖旧版审计表中 IN-007、EN-002、EN-003、UP-005、UP-006、RT-003 六项的当前事实。旧表中的“未完成”描述保留为历史审计记录，不再作为 0.1.31 的状态来源。

| ID | 0.1.31 状态 | 证据与边界 |
| --- | --- | --- |
| EN-002 | 已完成 | `CodeGraphEngine` 只使用真实 `codegraph-public-cli`；无 builtin fallback；真实 CodeGraph 1.5.0 已通过 init/files/query/trace/impact/affected 验证。 |
| EN-003 | 已完成基础契约 | `search_symbols/get_source/trace` 已进入 Adapter 和 MCP 主链路，公开符号身份统一为 `path::qualifiedName`；后续仍需扩充跨语言真实项目覆盖。 |
| UP-005 | 已完成基础交付 | `ProjectService.install` 管理 `post-checkout/post-merge/post-rewrite/post-commit`，使用 marker 保留用户 hook 内容，并支持 linked worktree 的真实 hooks 目录。 |
| UP-006 | 已完成基础状态机 | `git-event` 统一记录事件；支持 checkout、merge、rewrite、detached HEAD、非祖先 reset 的 sync/rebuild 补偿；失败状态暴露为 `reconciliation_required`。共享 daemon、复杂冲突自动恢复仍不在本版本范围。 |
| IN-007 | 已完成基础交付（0.1.32） | `FrameworkIndex` 只消费 CodeGraph 公共契约；FastAPI、Flask、Django、Lua/Skynet profile 已输出入口、注册点、生命周期、confidence、逐条来源和 unknowns。 |
| RT-003 | 已完成基础交付（0.1.33） | `embeddings: local` 使用确定性离线 provider 和 SQLite 向量索引；默认 disabled，网络 provider 不在本批范围。 |

0.1.31 验收证据：`tests/test_watch_wp07.py` 覆盖 10 项 Git 生命周期、失败可观测性、用户 hook 保留和 linked worktree；全量 pytest 通过；`scripts/validate_ci_workflow.py` 通过。生成知识已在 main 工作区同步，curated knowledge 仍需在后续框架索引和向量检索实现后逐项复核。

# 0.1.32 当前交付校正：IN-007 框架感知结构索引

IN-007 已完成基础交付。`FrameworkIndex` 只消费 CodeGraph 公共 `snapshot/search_symbols/get_source` 契约，不恢复 builtin parser；首批 profile 覆盖 FastAPI、Flask、Django 和 Lua/Skynet，输出 marker、入口、注册点、生命周期、confidence、逐条来源和 unknowns。测试、脚本和 profile 定义文件不作为应用框架 marker 来源。`KnowledgeGenerator` 将结果写入 `generated.frameworks`/`frameworks.md`，普通 knowledge search/context 可检索该记录。

验收证据：`tests/test_frameworks.py` 包含四类框架正例、通用 route 负例、生成记录契约和真实 CodeGraph FastAPI 临时项目；检索、评测与单目录相关聚焦测试通过。动态注册、反射、运行时服务发现仍明确属于 unknown，不得视为静态事实。

RT-003 的旧状态记录到 0.1.32 为未完成；以下 0.1.33 交付校正覆盖该历史结论。

# 0.1.33 当前交付校正：RT-003 可选向量检索

RT-003 已完成基础交付。`EmbeddingProvider`、`VectorIndex` 和确定性的 `DeterministicLocalProvider` 已接入 ProjectService 初始化/重建/同步生命周期；默认 `embeddings: disabled` 不加载 provider、不写向量表，`embeddings: local` 使用离线固定维度向量。SQLite Schema 从 v3 迁移到 v4，支持内容哈希、provider/model/维度失效、删除补偿、provider unavailable 与非法维度 fallback。

`KnowledgeAPI.search/context` 暴露 `vector_retrieval` 诊断。向量候选只能补充 lexical 结果，明确 lexical 命中和 CodeGraph 文件/符号结构证据保持优先；未接入网络模型或第三方 embedding。测试覆盖 disabled 零加载、确定性、哈希/model/维度失效、删除、fallback、Schema 迁移和 hybrid 排序契约；0.1.33 全量 pytest 为 285 项通过。

# 0.1.37 当前交付校正：WP-RQ-02 多路候选召回

WP-RQ-02 已完成候选召回层实现，但最终检索质量计划仍未完成。原查询词被完整保留，并增加可审计的确定性别名；路径精确、符号精确、符号别名、词法、知识、直接图关系、多跳图关系、测试/配置八类召回通道分别限流，候选在 debug trace 中保留通道来源。待同步文件不会进入候选集。

gardenserver 冻结的 12 题 Phase 0 集上，文件召回由 0.583333 提升至 0.833333，核心文件召回由 0.583333 提升至 0.791667，符号召回由 0.700000 提升至 0.800000，nDCG@5 由 0.535890 提升至 0.687006。新增 20 题挑战集的文件召回为 0.841667、核心文件召回为 0.816667、符号召回为 0.708333、nDCG@5 为 0.654721。

本批不宣称通过最终质量门：样本量仍少于 300，稳定仓库/快照仍少于 3，且业务不变量与同域组件排序仍存在失败样本；平均返回文件数上升也需要由 WP-RQ-03 排序阶段治理。完整复现证据见 `evaluation/reports/gardenserver-phase1-0.1.37.json`，需求状态见 `docs/retrieval-quality-work-package.md`。

# 0.1.38 当前交付校正：WP-RQ-03 符号优先与分查询类型排序

WP-RQ-03 的确定性排序基线已完成。默认 `policy-v2` 将开发任务意图与检索查询画像分离，先对显式限定符号、专用别名和定义命中进行符号排序，再按调用路径、影响范围、扩展点、不变量、设计原因、配置、测试/配置和工作流画像进行文件重排。通用符号、vendor/generated、高连接节点、robot 辅助镜像和无业务匹配的测试噪声均带可解释降权；测试查询的 Core 同时保留实现和相关测试。`retrieval.ranking_policy: policy-v1` 保留为回滚开关。

gardenserver 冻结 12 题集的文件召回、核心文件召回、符号召回和成功率均为 1.000000，nDCG@5 为 0.938488；20 题挑战集的文件召回、符号召回和成功率均为 1.000000，核心文件召回为 0.966667、核心文件精确率为 0.300000、nDCG@5 为 0.738156。两套评测均无排序回退。

0.1.38 当时的最终质量计划仍未完成：只有同一 gardenserver 快照的 32 题，且端到端 P95 超过 12 秒；当时尚未实现原方案 `precision@5`，不能用 `core_file_precision` 代替。该历史复现证据见 `evaluation/reports/gardenserver-phase2-0.1.38.json`；0.1.46 的实践门复核与正式门边界以本文开头的当前结论为准。
