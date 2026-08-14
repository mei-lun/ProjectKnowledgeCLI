# 0.1.27 交付闭环、CodeGraph 主链路与检索精确率设计

日期：2026-08-14  
目标版本：0.1.27  
工作包：WP-11  
状态：待用户书面复核

## 1. 背景与结论

0.1.26 修复了提交对齐的状态语义、评测来源记录、CI YAML 结构和本地包来源，但真实交付仍暴露三个相互关联的问题：

1. 最后一次知识同步发生在源码提交之前，导致提交后 `index_commit` 落后于 `HEAD`；
2. 本机虽安装 CodeGraph 1.5.0，PKS 的主检索链路仍使用 builtin/SQLite 事实，`codegraph` 评测还被无条件标记为不可用；
3. 当前检索通过宽泛符号、关系和知识来源扩展换取高召回，平均每题期望 2.2 个文件，hybrid 却返回约 41 个文件。

因此 0.1.27 不继续扩展外围特性，而是按“交付闭环 → 真实事实源 → 检索精确率”的顺序修复产品主链路。

## 2. 范围与非目标

### 2.1 本版本范围

- WP-11A：使源码提交、知识同步、生成产物提交和发布检查形成可重复闭环；
- WP-11B：将 CodeGraph 公共 CLI/API 接入文件、符号、调用关系、影响分析和受影响测试主链路；
- WP-11C：修正评测锚点并通过有界检索降低无关文件；
- WP-11D：更新审计、版本、评测基线和生成知识。

### 2.2 非目标

- 不读取 CodeGraph 私有数据库；
- 不把 CodeGraph 不可用时的 builtin 结果标记为 CodeGraph 结果；
- 不引入向量数据库、常驻 daemon、Web UI 或生产 Model Provider；
- 不自动执行 Git commit、push 或修改业务源码；
- 不以降低现有召回率、成功率或放宽冻结门禁换取精确率提升；
- 不在本版本实现 Lua/Skynet 动态运行时采集。

## 3. 方案选择

### 3.1 提交对齐

候选方案：

1. Git `post-commit` 自动同步：自动化程度高，但会在每次提交后修改工作树，依赖本地 Hook 安装且难以在所有客户端复现；
2. 放宽 `commit_aligned`：表面消除告警，但会失去“索引确实基于当前提交生成”的安全语义；
3. 显式发布收尾命令与 CI 检查：保留严格语义，向用户返回下一步动作，并用端到端测试保证发布顺序。

选择方案 3。新增 `project-kb finalize`，它不创建 Git 提交，只执行或检查知识收尾：

- 工作树包含非 PKS 生成修改时拒绝收尾，提示先提交源码；
- 工作树干净且索引落后时执行 `sync`，把索引基线对齐当前源码提交；
- 同步产生生成文件后返回 `generated_commit_required` 和精确文件列表；
- 用户提交这些生成产物后再次运行，只有 `verification_aligned=true`、无陈旧/冲突知识且无非生成修改时返回 `ready`；
- `--check` 只检查、不写入，供 CI 和发布前验证使用。

完整发布序列固定为：

```text
源码修改与测试
→ 提交源码
→ project-kb finalize
→ 审核并提交 PKS 生成产物
→ project-kb finalize --check
→ ready
```

### 3.2 CodeGraph 主链路

候选方案：

1. 保持 builtin 为主，CodeGraph 只做可选旁路：改动最小，但继续违背“CodeGraph 提供代码事实”的产品目标；
2. 完全移除本地存储中的符号和关系：边界纯粹，但会破坏离线能力、生成知识和现有兼容性；
3. CodeGraph 作为在线事实权威，本地 SQLite 作为知识、快照和兼容缓存：查询时使用 CodeGraph，生成记录继续保留可追踪快照，外部不可用时明确失败或由用户显式选择 builtin。

选择方案 3。

`CodeIndexEngine` 的公共返回契约保持稳定：

- `search_symbols(...) -> list[Symbol]`；
- `trace(...) -> list[Relation]`；
- `impact(...) -> {affected_files, affected_symbols, affected_modules, affected_tests, relations, limitations}`；
- `affected_tests(...) -> list[str]`；
- `status() -> {engine, available, adapter_version, capabilities, limitations, reason?}`。

`CodeGraphEngine` 必须把 CodeGraph 1.5 公共 JSON 归一化到上述结构。`KnowledgeAPI.context` 在 `engine=codegraph` 时通过引擎查询符号和影响关系；`KnowledgeAPI.impact` 同样委托引擎。SQLite 继续保存知识记录和快照，但不能在响应中把 builtin 关系冒充 CodeGraph 关系。

`CodeGraphEngine.parse()` 可以保留 builtin 静态解析作为兼容缓存生成器，但其产物必须标记为 `builtin-cache`，且不得成为 `engine=codegraph` 查询结果的事实来源。

### 3.3 检索精确率

检索改为分阶段漏斗：

1. 精确或前缀符号命中，优先稳定 ID 和完整标识符；
2. 对高置信锚点执行一次有界关系扩展；
3. 只加入与任务词、命中符号或影响文件有直接交集的知识记录；
4. 只有前三级证据不足时才扩大候选，不默认收集模块文档的全部来源；
5. 返回结果说明每个文件来自“直接符号、关系扩展、知识来源或回退搜索”中的哪一级。

删除当前评测中的双重影响扩展：hybrid 不再把所有知识来源符号重新送入一次全量 `impact`。Markdown 来源文件上限从固定 23 改为按分数差距截断，默认不超过 8 个。

## 4. 需求与验收标准

### 4.1 WP-11A：发布对齐

| ID | 需求 | 验收标准 |
| --- | --- | --- |
| REL-001 | 提交后可确定性收尾 | 临时 Git 仓库完成“源码提交 → finalize → 生成提交 → finalize --check”，最终返回 `ready` |
| REL-002 | 不隐藏源码未验证状态 | HEAD 含非生成修改且索引落后时返回非零退出码和 `source_commit_required`/`sync_required` |
| REL-003 | 不自动提交 | finalize 不执行 `git add`、`git commit`、`git push` |
| REL-004 | CI 检查真实发布状态 | CI 在测试和评测后运行只读收尾检查；失败输出包含 HEAD、index commit 和阻断文件 |
| REL-005 | 状态语义保持兼容 | `commit_aligned` 仍表示严格相等；`verification_aligned` 仅允许生成产物提交 |

### 4.2 WP-11B：真实 CodeGraph Adapter

| ID | 需求 | 验收标准 |
| --- | --- | --- |
| CG-001 | 检测真实 CLI | doctor/status 报告 CodeGraph 1.5.0 命令、版本、项目初始化状态和不可用原因 |
| CG-002 | 公共契约归一化 | files/query/callers/callees/impact/affected/node 的真实或固定 CLI 输出通过同一契约测试 |
| CG-003 | 查询主链路接通 | `engine=codegraph` 时 context/impact 的符号和关系来自 `CodeGraphEngine`，测试必须在 builtin SQLite 关系为空时仍成功 |
| CG-004 | 明确失败 | CLI 缺失、项目未初始化、JSON 非法、超时和契约缺字段均返回结构化原因，不回退 builtin |
| CG-005 | 真实集成验证 | 本机 CodeGraph 1.5.0 在临时代表性项目上完成 init、files、query、trace、impact 和 affected 验证，不修改源项目 |
| CG-006 | 评测真实可用性 | `strategy=codegraph` 不再硬编码不可用；仅在 Adapter 探测失败时返回 `adapter_unavailable` |

### 4.3 WP-11C：评测与精确率

| ID | 需求 | 验收标准 |
| --- | --- | --- |
| RET-001 | 修复真实锚点 | 40 题逐条核对当前源码；CodeGraph 失败题引用 `codegraph.py` 和 ADR-0002 |
| RET-002 | 有界 hybrid | 平均返回文件数较 0.1.26 的 40.95 至少下降 50%，hybrid 文件召回不低于 0.94 |
| RET-003 | 精确率门禁 | hybrid 文件精确率不低于 0.12，code 不低于 0.20，markdown 不低于 0.12，grep 不低于 0.29 |
| RET-004 | 成功率不退化 | hybrid/code 成功率分别不低于 0.40；不变量、设计原因和调用路径保留现有最低阈值 |
| RET-005 | 可解释来源 | 每个返回文件包含选择阶段或原因；精确命中与回退扩展可区分 |
| RET-006 | CodeGraph 独立基线 | CodeGraph 可用时生成独立指标；不可用不得阻断 builtin 离线回归，但正式发布必须附带真实 Adapter 验证结果 |

## 5. 数据流

### 5.1 发布收尾

```text
Git HEAD/工作树
    → FinalizationService 检查非生成修改
    → ProjectService.sync 对齐当前源码提交
    → 生成知识/manifest/评测状态变化
    → 用户审核并提交生成产物
    → FinalizationService --check
    → ready 或结构化阻断原因
```

### 5.2 CodeGraph 检索

```text
MCP/CLI task
    → KnowledgeAPI 任务分类
    → CodeGraphEngine.search_symbols
    → CodeGraphEngine.impact/trace
    → 归一化 Symbol/Relation/affected files
    → KnowledgeStore 匹配相关已审核知识
    → 有界合并、排序、解释
    → MCP/CLI 响应
```

## 6. 错误与安全边界

- CodeGraph 命令只通过公开 CLI 执行，项目路径必须显式传入；
- 临时真实集成测试复制最小项目或创建临时夹具，不在业务仓库写 `.codegraph`；
- 外部命令使用固定超时、UTF-8 解码和非零退出码检查；
- 归一化层拒绝项目外路径和缺少身份字段的符号；
- finalize 默认不写 Git，不接触远端，不删除用户文件；
- 任何不可验证事实进入 `limitations/unknowns`，不能标记为 verified。

## 7. 测试策略

按 TDD 分三组推进：

1. 发布闭环测试：真实临时 Git 仓库，不 mock Git；覆盖干净源码提交、生成产物提交、非生成提交、分支变化和只读检查；
2. Adapter 契约测试：固定 CLI 夹具覆盖所有公共命令、字段缺失、超时、非法路径和失败退出码；再运行可选但发布必需的 CodeGraph 1.5.0 真实集成脚本；
3. 检索评测测试：先固定平均返回文件数和精确率失败样本，再实现有界选择；全量 40 题和 WP-01/WP-02 数据集均不得降低既有召回阈值。

最终验证至少包括：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\validate_ci_workflow.py
.venv\Scripts\python.exe scripts\validate_codegraph_adapter.py
.venv\Scripts\python.exe -m project_knowledge evaluate evaluation\questions.jsonl --project . --strategy all --thresholds evaluation\thresholds.json --baseline evaluation\baselines\self-repo-0.1.26.json --output evaluation\reports\latest.json --quiet
.venv\Scripts\python.exe -m project_knowledge finalize . --check --json
.venv\Scripts\python.exe -m project_knowledge --version
```

## 8. 文档、版本和知识同步

- 唯一版本源递增到 0.1.27，CHANGELOG 记录 WP-11；
- `docs/project-knowledge-system-audit.md` 将 P0-GIT-001 改为“部分完成”，只有 REL-001～REL-005 全部通过后才恢复“已完成”；
- `docs/next-version-plan.md` 以 WP-11A/B/C/D 和本规格的需求 ID 为当前计划；
- ADR-0002 更新为真实 Adapter 已接入后的最终边界，不能保留“尚未实现”与“已完成”并存；
- 生成知识在源码提交之后同步，最终使用生成产物提交进入 `verification_aligned`；
- 7 条 potentially stale 的 curated/decision 知识逐条复核；无法确认的记录保持待复核，不伪造通过。

## 9. 完成定义

只有满足以下全部条件，0.1.27 才能标记完成：

- REL-001～REL-005、CG-001～CG-006、RET-001～RET-006 均有正负自动化证据；
- 本机真实 CodeGraph 1.5.0 集成脚本通过；
- 全量单元/集成测试通过；
- 40 题全策略门禁通过且精确率达到新阈值；
- 版本、CHANGELOG、审计、下一版本计划和 README 一致；
- 生成知识已同步，最终发布检查返回 `ready`；
- curated knowledge 的复核结果被明确记录。
