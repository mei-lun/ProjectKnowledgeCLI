# 约定

0.1.30 人工复核：项目和默认配置只允许 `codegraph`；不可用或未初始化时明确失败；公共符号引用使用 `path::公开符号名`，不得暴露内部哈希 ID。

- 保持与 Python 3.11+ 兼容，不强制依赖网络服务或第三方运行时软件包。
- 保持确定性生成事实与经过评审的人工意图相互分离。
- 自动生成知识、知识索引和初始人工模板默认使用中文；代码标识、路径、记录 ID 和机器接口枚举不得为了显示翻译而改变。
- 项目以 `0.1.0` 为版本基线。后续每批修改或新增内容都通过 `python scripts/bump_version.py "中文变更说明"` 更新版本和 `CHANGELOG.md`；同一批修改或新增内容只递增一次补丁版本，由该批变更触发的知识同步不重复递增。
- 后续功能开发以 `docs/project-knowledge-system-audit.md` 的工作包、需求 ID 和验收条件为基线；没有实现证据、正负测试和相关评测的条目不得标记为完成。
- 每个工作包必须更新相关评测问题和失败样本。冻结阈值不得为通过 CI 而降低；确需调整时必须记录新证据、原因和版本。
- 不同检索策略使用独立阈值；CodeGraph 不可用时必须报告 `adapter_unavailable`，不得用本地 parser 或 SQLite 旧表结果冒充。
- CodeGraph Adapter 只使用公共 CLI/API；不透明内部 ID 必须在边界内转换为公共符号引用，SQLite 缓存为空不能阻断实时引擎查询。
- 交付必须先提交源码和文档，再用 `project-kb finalize` 同步生成物；生成物提交后以 `finalize --check` 只读验证。该命令不得执行 `git add`、`git commit` 或 `git push`。
- 真实项目评测默认使用临时只读镜像，并以源目录全树快照前后一致作为未写入证据。
- 评测报告和冻结基线必须排除出被测索引，避免后一次运行检索到前一次答案而造成自污染。
- 0.1.3 首次稳定基线冻结 grep+Read 文件召回率/精确率下限 `0.67/0.29`、only-Markdown 文件精确率下限 `0.085`；后续不得无证据下调。
- 0.1.4 将最低快速集样本数提高到 25 且不降低指标阈值；只有数据集哈希相同的报告才能比较汇总回归，不同数据集只检查冻结的绝对门槛并明确告警。
- 0.1.5 将最低快速集样本数提高到 30，新增 Feature Guide Schema、语义生成、来源校验、草案生命周期和功能检索问题；既有指标阈值未降低，最终绝对门和同数据集相对回归门均通过。
- 0.1.27 在 40 题数据集上保持既有召回门槛，并冻结 hybrid/code/Markdown 文件精确率下限 `0.12/0.20/0.12`；评测锚点、阈值和真实 CodeGraph 验证必须同时有版本化证据。
- 0.1.28 交付复核已确认默认排除仓库内部 `.worktrees/**`；正式评测必须在干净工作区使用同数据集基线，CI 必须拒绝题集哈希不匹配的基线，实测指标只以版本化 JSON 报告为唯一来源，审计不重复抄录指标快照。
- 返回给 AI 客户端的知识必须包含相对来源路径、稳定符号 ID、可信度和新鲜度。
- 状态变更使用原子文件替换和单写入者锁；索引变更使用 SQLite 事务。
- 如果 `status` 已知某个来源正在等待同步，不得返回从该来源派生的旧内容。
- 内容新鲜度与 Git 提交对齐必须分别报告；`check` 只有在二者都满足时才能视为健康。
- 未经人工复核并移除 `project-kb:template` 标记的初始模板必须保持 `inferred`，不得标记为 `verified`。
- 模块文档达到展示上限时必须报告总量、已展示数量和继续查询入口，不得静默截断。
- 结构化 KnowledgeRecord 和 ChangeSet 在落盘前必须执行运行时 Schema 验证。
- Provider 默认 disabled 且禁止网络；dry-run 永不执行请求。非本机 HTTP Provider 必须同时关闭 local_only、使用 HTTPS、显式启用网络并提供精确外发授权短语。
- EvidencePack 只使用项目内相对路径，先排除高风险文件，再脱敏和裁剪；缓存、检查点、日志和错误不得保存 Secret 原值或未脱敏证据正文。
- Provider 输出必须先脱敏再通过调用方 Schema，缓存命中也必须重新验证；无效输出不得落入缓存或知识库。
- Feature Guide 模型输出只能是 `draft`。每条确定性陈述必须至少包含一个来源；无来源判断进入 `unknowns`。来源必须属于 EvidencePack，并在缓存和草案写入前通过路径、行号、文件/符号存在性和哈希校验；已有文档只能作为 `candidate`。
- draft 即使新鲜也必须提示读取实时源码；只有后续受控 Proposal 审核才能将语义知识提升为 `verified`。
- 未审核 Proposal 只能写入 `.project-kb/proposals/`，不得修改 curated。Proposal 必须使用稳定 ID、结构化 Patch operation、目标哈希和可解析来源哈希；apply/reject 必须记录审核人、时间和中文理由。
- curated 自动变更只允许发生在明确的 `project-kb:generated` block 内。删除 block 必须同时记录 `deleted_sources` 和 `supersedes`；目标或来源变化后旧提案必须冻结为 `conflicted`。
- ADR 只允许通过 Proposal 创建新的中文草案。已有 ADR 不得由自动化静默改写，废弃决策通过新 ADR 的替代关系表达。
- Semantic Update Queue 只由源码文件的新增、修改或删除触发；纯 curated/ADR 审核同步不得产生新的语义待办。
- 0.1.6 最终复核确认：队列过滤会排除 `.github/`、`docs/`、`evaluation/`、`tests/` 和 `.project-kb/`，避免工具自身产物反向触发语义更新。
- 0.1.6 将最低快速集样本数提高到 35，新增 Proposal 稳定 ID、应用冲突、草案提升、删除/ADR 和 Semantic Update Queue 问题；冻结能力阈值没有下调。
- 精确符号命中必须优先于模糊匹配；only-Markdown 读取页数和相关正文必须服从总 Token 预算。
- 长知识文档的任务词命中必须优先于通用安全关键词加权；“必须/验证”等提示只能在相关性相近时提高不变量片段的优先级，不能挤掉直接任务证据。
- 同文件重复符号的首个定义保留普通 ID，后续定义使用稳定 `@line` 后缀；任何解析器不得向 SQLite 提交重复符号 ID。
- 使用 `unittest` 为生命周期、新鲜度、隐私、MCP 和标记所有权变更添加回归覆盖。
- 任务上下文不得超过请求的总 Token 预算，排序后的知识页面最多返回四个。
- 保留由标记边界保护的集成块周围的用户自有内容。
- 完整重建期间保留人工知识和 ADR 的来源哈希基线，不得把索引重建视为人工验证。

<!-- project-kb:source file="pyproject.toml" -->
<!-- project-kb:source file="src/project_knowledge/versioning.py" -->
<!-- project-kb:source file="scripts/bump_version.py" -->
<!-- project-kb:source file="docs/project-knowledge-system-audit.md" -->
<!-- project-kb:source file="tests/test_integration.py" -->
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="src/project_knowledge/util.py" -->
<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
<!-- project-kb:source file="src/project_knowledge/finalization.py" -->
<!-- project-kb:source file="src/project_knowledge/codegraph.py" -->
<!-- project-kb:source file="src/project_knowledge/retrieval.py" -->
<!-- project-kb:source file="src/project_knowledge/real_project.py" -->
<!-- project-kb:source file="src/project_knowledge/config.py" -->
<!-- project-kb:source file="evaluation/thresholds.json" -->
<!-- project-kb:source file="src/project_knowledge/evidence.py" -->
<!-- project-kb:source file="src/project_knowledge/provider.py" -->
<!-- project-kb:source file="src/project_knowledge/semantic.py" -->
<!-- project-kb:source file="src/project_knowledge/schemas.py" -->
<!-- project-kb:source file="tests/test_semantic.py" -->
<!-- project-kb:source file="src/project_knowledge/proposal.py" -->
<!-- project-kb:source file="tests/test_proposal.py" -->

<!-- project-kb:generated id="retrieval-contract" -->
- 0.1.9 WP-07：watcher 必须使用每项目单协调锁、记录 PID/heartbeat/结构化日志；同步必须进行二次哈希校验，分支变化必须单独报告并补偿；hooks 只能写入工具拥有的标记区块。
- 0.1.10 WP-08：配置升级必须先支持 dry-run，保留未知用户字段，并对高于当前版本的配置显式失败；Claude、Cursor、Gemini 适配必须幂等且卸载只删除工具拥有区块；补丁升级必须同步核心包、CHANGELOG 与 Codex 插件版本。
- 0.1.11 WP-09：中文任务到标识符的扩展必须使用显式短语词元表，完整 snake_case 组合优先于宽泛分词；新增映射必须有召回回归和 Token 预算断言，不得降低冻结质量阈值。
- 发布不变量：同一发布批次的核心包版本与 Codex 插件版本必须一致。
- 0.1.12 WP-09：长知识文档必须按任务相关性截取并保持 Token 上限；不变量、回滚和验证行不能因固定首段裁剪被静默遗漏。
- 0.1.13 WP-09：安全约束行可获得片段优先级加成，但仍受总 Token 预算、来源追踪和实时源码核验约束。
- 0.1.14 WP-09：同一批修改或新增内容只递增一次补丁版本；版本知识必须同时引用核心版本文件和 CHANGELOG，不能只依赖版本工具实现。
<!-- project-kb:source file="src/project_knowledge/__init__.py" -->
<!-- project-kb:source file="CHANGELOG.md" -->
- 0.1.15 WP-02：Lua/Skynet 入口检索必须区分已检测的 Skynet 启动、协议派发和仅按文件名推断的入口；后两类动态语义必须进入 unknowns 或人工确认。
<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/real_project.py" -->
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
- 0.1.15 WP-09：空结果的 generated 页面仍必须引用生成器/解析器实现来源；Markdown 选页仅在源码模块候选达到第三页相对得分 0.8 时替换低优先页面，不得扩大三页上限或降低冻结阈值。
<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
<!-- project-kb:source file="tests/test_evaluate.py" -->
<!-- /project-kb:generated -->

## WP-12A 评测约定（0.1.29）

严格 core 指标只使用数据集的 `expected_files` 与有序 `core_files`；`acceptable_supporting_files` 只产生诊断性的 supporting 精确率，不改变成功语义。正式评测要求 `ranking_fallback_rate == 0`。WP-12A 的 50 条数据、阈值和报告是唯一指标来源；dirty 工作树或 stale 索引运行只能作为诊断，不能宣称质量门通过。

<!-- project-kb:source file="src/project_knowledge/evaluate.py" -->
<!-- project-kb:source file="evaluation/thresholds.json" -->
