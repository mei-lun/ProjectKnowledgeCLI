# Project Knowledge MCP 全链路审计日志设计

## 1. 背景与目标

Project Knowledge CLI 已在 `knowledge_context`、`knowledge_search` 和 `knowledge_impact` 返回值中提供部分 `retrieval_trace`，评测模块也能基于冻结数据集计算 precision、recall、MRR 和 nDCG。然而，当前 MCP 边界没有持久记录完整请求、完整响应、协议错误和跨工具调用顺序，内部 trace 也没有统一关联到一次 MCP 调用。因此，现有数据不能稳定回答以下问题：

1. Agent 在一个 MCP 会话中依次调用了哪些 Project Knowledge 工具；
2. 每次调用接收了什么参数，返回了什么数据，耗时和错误是什么；
3. 检索、排序、证据裁剪、CodeGraph 和 Provider 调用如何形成最终响应；
4. 一条失败或低质量结果是来自参数、依赖、召回、排序、预算还是协议层；
5. 导出的观测数据是否完整，能否与人工 ground truth 稳定关联后计算质量指标。

本设计新增工作包 `WP-OBS-01`，建立本地、追加写、可验证完整性的 MCP 全链路审计日志。日志不限制文件大小，不轮转，不上传。原始调用事实与人工标注分离，以稳定 ID 关联。

## 2. 工作包与需求 ID

| ID | 要求 | 验收证据 |
| --- | --- | --- |
| OBS-001 | 记录 MCP 收到的每一条消息及对应完整响应 | 协议级正负测试和真实 stdio 端到端样本 |
| OBS-002 | 每个会话、调用和内部阶段具有稳定关联 ID 与顺序 | 多调用、重复 JSON-RPC ID、通知和多会话测试 |
| OBS-003 | 记录工具分派、检索 trace、CodeGraph 和 Provider 依赖调用 | 嵌套 span 测试和依赖失败测试 |
| OBS-004 | 成功、业务错误、协议错误和进程中断均可判断是否闭合 | 错误闭合、缺失 session end 和日志写失败测试 |
| OBS-005 | 日志保留完整分析载荷，但 Secret 必须强制脱敏 | 伪 Token、Bearer、私钥、授权字段和嵌套响应测试 |
| OBS-006 | 将原始事件确定性导出为一行一次调用的分析 JSONL | 顺序无关重组、重复导出和 Schema 测试 |
| OBS-007 | 导出时验证序列连续性、调用闭合和 span 树完整性 | 完整、截断、重复、孤儿 span 和损坏 JSONL 测试 |
| OBS-008 | 导出预测字段可与独立 ground truth 关联并计算质量指标 | 真实 MCP 样本到评测输入的端到端评测样本 |

只有实现、正负测试、端到端样本、相关评测、文档、版本递增和知识同步全部完成后，才能在审计报告中将这些需求标记为已完成。

## 3. 范围与非目标

### 3.1 范围

- Project Knowledge MCP stdio 服务器收到和返回的 JSON-RPC 消息；
- `initialize`、`server/discover`、`ping`、`tools/list`、`tools/call`、客户端通知、未知方法和无效 JSON；
- 所有 Project Knowledge MCP 工具，不限于五个只读知识工具；
- 工具分派和现有检索 `retrieval_trace`；
- MCP 调用期间触发的 CodeGraph CLI 和 Provider 请求；
- 本地 JSONL 原始事件、完整性验证和分析数据导出；
- 与人工或冻结数据集 ground truth 的稳定关联字段。

### 3.2 非目标

- 不记录 Agent 的对话、推理、终端命令或其他 MCP Server 行为；
- 不推断 Agent 没有显式提供的任务意图；
- 不因为相邻调用时间接近就宣称两次调用存在因果关系；
- 本工作包不建设质量仪表盘、远程遥测、集中采集或日志轮转；
- 不记录 SQLite 的每条 SQL，也不把内部函数调用全部转换为 span；
- 不用生产日志自动生成 ground truth，ground truth 必须来自独立人工确认或冻结评测集。

## 4. 总体架构

新增 `observability.py`，作为独立于 MCP、检索和依赖适配器的审计边界。它包含四个职责明确的组件：

1. `AuditContext`：使用 `contextvars` 保存当前 `session_id`、`invocation_id`、`span_id` 和调用序号，使下游 CodeGraph/Provider 不需要依赖 MCPServer；
2. `MCPAuditLogger`：生成事件、统一脱敏并追加写入 JSONL；
3. `AuditSpan`：上下文管理器，成对写入内部阶段的开始和结束/失败事件；
4. `AuditExporter`：读取原始事件，验证完整性，并按一次调用聚合为分析记录。

依赖方向为：

```text
MCPServer -> observability <- retrieval / CodeGraph / Provider
                         -> raw JSONL
raw JSONL -> AuditExporter -> analysis JSONL
ground truth JSONL ---------> 后续离线评测
```

`observability.py` 不导入 MCP、检索、CodeGraph 或 Provider 实现，避免形成循环依赖。业务模块只依赖其窄接口。

## 5. 关联模型

MCPServer 启动时使用标准库 UUID4 生成 `session_id`，并写入 `session_started`。服务器读到的每一条非空输入行都占用一个单调递增 `sequence`，包括通知、无效 JSON 和未知方法。每条输入另生成全局唯一 `invocation_id`，不能直接复用客户端 JSON-RPC `id`，因为客户端可能重复使用 ID。

一次工具调用的根 span 使用 `invocation_id` 作为 `trace_id`，内部阶段生成 `span_id` 并保存 `parent_span_id`。分析链路字段为：

- `session_id`：一个 MCPServer 进程内的连接会话；
- `sequence`：该会话读取消息的严格顺序；
- `invocation_id`：一次输入消息的唯一身份；
- `previous_invocation_id`：同一会话前一条输入，只表达顺序，不表达因果；
- `client_request_id`：原始 JSON-RPC `id`，保留其原始 JSON 类型；
- `client_trace`：若客户端在 `params._meta` 提供 trace/task 标识则原样脱敏保存，否则为 `null`；
- `trace_id`、`span_id`、`parent_span_id`：内部阶段树。

由于 MCP 协议不保证提供 Agent task/thread ID，系统只能确定同一服务器会话内的顺序。导出数据必须显式包含 `causality: "ordered_only"` 或 `causality: "client_correlated"`，不得把推断链路冒充客户端确认链路。

## 6. 原始事件 Schema

原始日志为 JSONL，每行一个 `audit-event-v1` 对象，通用字段如下：

```json
{
  "schema_version": 1,
  "event_id": "evt_...",
  "event": "invocation_completed",
  "timestamp": "2026-09-01T12:00:00.000000+08:00",
  "monotonic_ns": 123456789,
  "project_id": "sha256:...",
  "project_root": "D:/Github-Poj/ProjectKnowledgeCLI",
  "server_version": "0.1.59",
  "protocol_version": "2026-07-28",
  "pid": 1234,
  "session_id": "ses_...",
  "sequence": 7,
  "invocation_id": "inv_...",
  "trace_id": "inv_...",
  "span_id": null,
  "parent_span_id": null,
  "payload": {}
}
```

事件类型及最低载荷：

| 事件 | 最低载荷 |
| --- | --- |
| `session_started` | server 信息、进程、项目、日志策略 |
| `client_initialized` | initialize 中实际收到的 client 信息、能力和协商协议 |
| `message_received` | 原始解析结果；无效 JSON 保存原始行和解析错误 |
| `invocation_started` | method、tool name、完整脱敏 params/arguments |
| `span_started` | kind、name、输入、cache 状态 |
| `span_completed` | kind、name、完整输出、状态、耗时 |
| `span_failed` | kind、name、错误类型、错误消息、可用 stdout/stderr、耗时 |
| `invocation_completed` | 完整 JSON-RPC response、工具结构化结果、状态、耗时 |
| `invocation_failed` | 完整 JSON-RPC error/result、错误分类、耗时 |
| `notification_observed` | method、完整 params、无响应原因 |
| `audit_gap` | 丢失的内存序号范围、写失败类型和恢复时间 |
| `session_ended` | 最后序号、调用计数、成功/失败数、audit health |

日志中的 `message_received` 保存解析后的 JSON 对象；只有 JSON 解析失败时保存原始文本。`invocation_completed` 保存实际写往 stdout 的 JSON-RPC 响应经过统一脱敏后的对象，并保存实际响应字节的整体 SHA-256，不重新构造近似响应。非敏感响应可逐字段核对；含 Secret 的响应只能核对整体哈希和脱敏位置，不能在日志中复原 Secret。

## 7. 采集边界

### 7.1 MCP 协议边界

`MCPServer.serve` 负责所有输入行的会话顺序、解析失败和最终 stdout 响应采集。`MCPServer.handle` 在有结构化请求时写调用开始，并在每条返回路径上闭合调用。通知必须写 `notification_observed`，即使现有行为是不返回响应。

审计日志只写文件和 stderr 诊断，绝不写 stdout，避免破坏 MCP JSONL 协议。

### 7.2 工具分派与检索

`MCPServer._call` 使用 `tool_dispatch` span 包裹全部工具。对于返回 `retrieval_trace schema v2` 的工具，完整返回天然进入调用响应；同时将阶段摘要标准化为嵌套 `retrieval` span，保留：

- 查询扩展和召回通道；
- 候选、候选来源和排序分数；
- CodeGraph 关系与证据来源；
- 裁剪前后 evidence 快照和裁剪事件；
- 最终文件、符号、知识记录、缺口与状态；
- 每个已有阶段的耗时和错误。

内部 span 不能读取评测 ground truth，也不能改变召回、排序或 Token 预算行为。

### 7.3 CodeGraph CLI

`CodeGraphClient._run` 是统一外部命令边界。每次真实进程执行写 `dependency.codegraph` span，记录脱敏后的 argv、项目工作目录、stdin、stdout、stderr、退出码、超时、解析后的 JSON 和耗时。命中请求级缓存时不伪造进程调用，而是写 `dependency.codegraph_cache` span，并关联原始命令键。

命令记录使用 argv 数组，不拼接 shell 字符串。路径保持调用时真实值，同时提供规范化的项目相对路径字段，方便跨机器聚合。

### 7.4 Provider

Provider 的统一生成入口写 `dependency.provider` span，记录 provider/model/prompt/schema/evidence/request hash、脱敏请求体、脱敏响应体、HTTP 状态或本地 provider 状态、重试序号、缓存命中和耗时。Authorization、API Key 和 Secret 永不写入。

## 8. 完整载荷与强制脱敏

“完整载荷”定义为：除安全敏感值外，保留 MCP 实际请求、实际响应、依赖输入输出和诊断字段，不按长度截断、不按 Token 预算裁剪、不只保存摘要。

脱敏是不可关闭的安全不变量。统一扫描以下位置：

- 大小写不敏感的 `authorization`、`api_key`、`token`、`password`、`secret`、`cookie` 字段；
- Bearer/Basic 凭据和已知 Token 格式；
- PEM 私钥；
- Provider 配置中的授权值；
- 嵌套对象、数组、stdout、stderr、异常文本和无效 JSON 原始行。

使用现有 `SecretScanner` 的规则作为基础，但审计层必须提供递归结构脱敏，并通过伪 Secret 正负样本证明普通源码词语不会被误删。脱敏后写 `redactions` 数组，记录规则名和 JSONPath，不记录原始值。

## 9. 存储、一致性与失败策略

默认路径为 `.project-kb/logs/mcp-events.jsonl`。继续复用项目现有 `.gitignore` 对 `.project-kb/logs/` 的排除，不把运行日志纳入知识索引或 Git。

单进程内使用互斥锁保证事件顺序。多 MCP 进程通过专用的短持有追加锁序列化单行写入，不复用 `.project-kb/write.lock`，避免与索引事务互相阻塞。每行一次编码、一次追加、flush 和 fsync；锁只覆盖单行追加，不覆盖工具执行。

日志失败采用“业务 fail-open、审计 fail-visible”：

1. 日志写失败不能污染 stdout，也不能改变原 MCP 成功/失败语义；
2. 失败立即写 stderr，并在内存记录丢失序号范围；
3. 下次写入恢复时先写 `audit_gap`；
4. `session_ended` 缺失表示会话可能异常终止；
5. 导出/验证对 gap、未闭合调用、未闭合 span、序列倒退、重复事件和损坏 JSON 返回非零退出码；
6. 不允许在完整性失败时静默生成可被误认为完整的质量数据。

## 10. 分析导出

新增命令：

```text
project-kb mcp-log validate --project <path>
project-kb mcp-log export --project <path> --output <analysis.jsonl>
```

`validate` 输出会话数、调用数、起止时间、事件数、缺口、未闭合调用、孤儿 span、损坏行和可分析状态。完整性失败退出码非零。

`export` 先执行同等验证，默认只导出闭合调用；若存在任何完整性问题则整体失败，不产生新的正式输出。输出使用临时文件和原子替换。每行一个 `mcp-analysis-v1` 调用对象，至少包含：

- 全部关联 ID、顺序、客户端信息、协议和服务版本；
- method、tool、arguments、response、status、error 和 duration；
- 有序 span 树及 CodeGraph/Provider 命令与返回；
- `prediction.returned_files`、`returned_symbols`、`returned_knowledge_ids`；
- `prediction.call_paths`、`extension_points`、`invariants`、`design_reasons`；
- 候选、排名、选择理由、缺口、context status 和 Token 数据；
- `ground_truth_ref`，默认等于 `invocation_id`；
- `causality` 和完整性状态。

导出器对事件文件顺序不作假设，而是按 ID 聚合后用 `session_id + sequence` 确定调用顺序。相同输入事件重复导出必须产生字节一致的主体数据；导出时间等运行元数据单独放在报告头或 sidecar，不能破坏可复现性。

## 11. 质量指标使用方式

日志提供 prediction，不提供 ground truth。标注文件以 `ground_truth_ref` 为键，沿用现有评测字段：

- `expected_files`；
- `acceptable_supporting_files`；
- `expected_symbols`；
- `expected_call_path`；
- `expected_extension_points`；
- `expected_invariants`；
- `expected_design_reasons`。

由此可以复用或扩展当前 `evaluate.py` 计算文件/符号/调用路径 precision 和 recall、MRR、nDCG、成功率及失败率。调用链合理性分析使用有序工具调用、错误、耗时和 span 树；只有 `client_trace` 明确关联时才计算跨调用因果指标，否则只报告顺序模式。

端到端验收必须使用真实 MCP JSONL 会话产生原始日志，导出 prediction，再与独立 ground truth 合并并跑出确定性指标。手工构造一个只含字段的空日志不能作为验收。

## 12. 兼容性与配置边界

本地审计日志默认启用，因为该工作包的目标是形成完整可审计数据。它不属于 `privacy.telemetry`：不发起网络请求，不改变 `telemetry: false` 的默认值，也不移除现有 unsupported telemetry 告警。

本工作包不新增可关闭脱敏的配置，不新增远程目的地，也不改变任何 MCP 工具的 input schema。客户端无需传新字段即可工作；若标准 `params._meta` 中已有 trace 信息则只读取并记录。

旧日志不存在时，`validate` 和 `export` 返回明确的 `no_audit_log`，不会将空集合报告为完整数据。旧版本生成的 `service.jsonl` 不混入 MCP 调用日志。

## 13. 测试与评测策略

按项目基线先补测试和真实样本，再实现行为。

### 13.1 单元测试

- 事件 Schema、ID、时间、顺序和确定性导出；
- 递归脱敏及误报负样本；
- span 正常闭合、异常闭合、嵌套和孤儿检测；
- CodeGraph 成功、非零退出、超时、无效 JSON 和缓存命中；
- Provider 成功、失败、重试、缓存和授权脱敏；
- 并发追加与损坏行检测。

### 13.2 MCP 协议测试

- initialize、discover、ping、tools/list；
- 每一个 `TOOLS` 注册项至少有一次可审计分派覆盖；
- 参数 Schema 错误、未知工具、未知方法和内部异常；
- 通知、空行、无效 JSON、重复 request id；
- 确认 stdout 只含合法 MCP 响应，日志不串入协议流；
- 确认非敏感日志响应与实际 stdout 响应逐字段相同；含 Secret 响应的整体哈希相同且敏感值只在日志中被替换。

### 13.3 端到端与评测样本

- 新增真实 stdio 会话夹具，至少包括 `knowledge_status -> knowledge_context -> knowledge_impact` 的成功/失败组合；
- 使用真实 CodeGraph 公共 CLI 的受控临时项目验证命令与返回 span；
- 从真实日志导出 analysis JSONL；
- 将独立标注与 prediction 关联，计算至少 file precision/recall、symbol precision/recall 和 call path precision/recall；
- 证明删掉一个事件后验证失败，不能继续产出正式分析集。

## 14. 预计代码边界

| 文件 | 责任 |
| --- | --- |
| `src/project_knowledge/observability.py` | 上下文、事件写入、span、脱敏、验证和导出核心 |
| `src/project_knowledge/mcp.py` | MCP 会话、消息和完整响应边界 |
| `src/project_knowledge/codegraph.py` | CodeGraph 命令/缓存 span |
| `src/project_knowledge/provider.py` | Provider 请求/响应 span |
| `src/project_knowledge/cli.py` | `mcp-log validate/export` 命令入口 |
| `src/project_knowledge/schemas.py` | audit event 和 analysis export Schema |
| `tests/test_mcp_observability.py` | 单元、协议和导出正负测试 |
| `tests/test_integration.py` | 真实 MCP 主链路集成覆盖 |
| `evaluation/` | 从真实调用导出的评测样本和报告 |
| `README.md`、`docs/evaluation-guide.md` | 使用、隐私、标注和指标说明 |
| `docs/project-knowledge-system-audit.md` | WP-OBS-01 需求状态与真实验收证据 |

实现中若发现检索阶段需要新增公共接口，必须先证明现有 `retrieval_trace` 无法提供该事实；不得为了日志重构检索排序行为。

## 15. 完成门槛

WP-OBS-01 只有同时满足以下条件才算完成：

1. OBS-001～OBS-008 均有正负测试；
2. 所有 MCP 工具调用均能闭合为可导出的 invocation；
3. CodeGraph/Provider 外部调用可关联到父 invocation；
4. 完整响应无长度截断，Secret 扫描测试无泄漏；
5. 完整性损坏会阻止正式导出；
6. 真实 MCP 会话可导出并与独立标注计算质量指标；
7. 全量测试和仓库文档验证通过；
8. 运行一次 `python scripts/bump_version.py "新增 MCP 全链路审计日志与质量分析导出"`；
9. `python -m project_knowledge --version` 与 `CHANGELOG.md` 对应；
10. 运行 Project Knowledge 同步并报告 generated knowledge 是否同步、curated knowledge 是否需要人工复核；
11. 最后才在审计报告中把需求标记为已完成。
