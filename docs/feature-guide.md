# Feature Guide、Workflow 与 Recipe 使用指南

本文说明如何从有限、脱敏、可追溯的源码证据生成中文功能开发草案。系统默认不会调用模型；只有 Provider 已显式启用并满足本地或云端授权规则时才执行生成。

## 1. 解决的问题

静态分析可以可靠回答“有哪些文件、符号和关系”，但不能单独给出完整的功能开发指导。WP-04 在静态事实层之上增加语义草案层，用来组织：

- 功能职责与入口；
- 当前工作流；
- 依赖、数据和状态；
- 业务不变量；
- 推荐扩展点；
- 实施、验证和回滚 Recipe；
- 已知陷阱与未决问题。

模型负责理解和组织，系统负责限制输入、校验结构、核对引用并控制生命周期。模型不能把自己的输出直接声明为已验证知识。

## 2. 生命周期

| 层级 | 含义 | 默认使用方式 |
| --- | --- | --- |
| `generated` | 可从源码确定性重建的项目图、模块图等 | 可作为代码事实，但仍检查新鲜度 |
| `draft` | 模型生成且通过结构和来源校验的 Feature Guide | 可用于导航；修改代码前必须读取实时来源 |
| `verified` | 人工或受控 Proposal 流程确认的语义知识 | 可作为主要开发依据 |

当前版本完成 `generated → draft`。`draft → verified` 必须走 WP-05 Proposal 审核，不能通过修改模型响应或 `lifecycle` 字段绕过。

## 3. 首次生成流程

### 3.1 初始化和发现候选

```bash
project-kb init /path/to/repository
project-kb feature-candidates --project /path/to/repository --json
```

候选由文件模块和结构符号确定性生成，包含来源文件和最多 20 个符号锚点。它只是语义生成的范围建议，不是已确认的业务功能边界。

### 3.2 预览 EvidencePack

```bash
project-kb generate "开发背包物品使用功能" \
  --project /path/to/repository \
  --file src/bag/service.py \
  --file tests/test_bag.py \
  --dry-run --json
```

预览不会执行 Provider。输出会列出相对文件、遗漏文件、估算 Token、脱敏计数、是否会使用网络以及所有策略阻断。应先确认范围中没有不必要的源码或敏感信息。

### 3.3 保存草案

```bash
project-kb generate "开发背包物品使用功能" \
  --project /path/to/repository \
  --file src/bag/service.py \
  --file tests/test_bag.py \
  --save-draft --json
```

成功后产生两个分片：

- `docs/knowledge/drafts/features/<feature-id>.md`：默认中文、便于阅读和检索；
- `.project-kb/drafts/features/<feature-id>.json`：结构化草案和生成元数据。

每个功能独立分片，不会把大型项目的全部功能压入一个文档。Markdown 会进入 Manifest 和 FTS 索引，JSON 保留 provider、model、prompt、Schema、EvidencePack 和请求哈希。

## 4. 结构约束

Feature Guide 必须包含以下字段：

- `feature_id`、`title`、`domain` 和固定的 `lifecycle: draft`；
- `summary`、`responsibilities`、`entrypoints`；
- 含连续步骤编号的 `workflow`；
- `dependencies`、`data_and_state`、`invariants`、`extension_points`；
- 含前置条件、实施、验证和回滚的 `recipe`；
- `tests`、`pitfalls`、`unknowns`。

除 `unknowns` 外，每条确定性陈述都是 `{text, sources}`，并至少有一个来源。模型没有证据支持的判断必须写入 `unknowns`，说明原因和需要补充的证据。

## 5. 来源校验

结构校验通过后，系统还会使用本地项目和 SQLite 索引执行第二层校验：

1. 路径必须是项目内相对路径，不能越过项目根目录；
2. 引用文件必须存在且包含在本次 EvidencePack；
3. 文件来源哈希必须等于 EvidencePack 中的脱敏证据哈希；
4. 符号 ID、所属路径、定义行范围和符号哈希必须与实时索引一致；
5. 引用行号不能超出文件范围；
6. Markdown、RST 和文本等已有文档只能使用 `authority: candidate`；
7. Workflow 的 `order` 必须从 1 连续递增。

两层校验都在知识文件和 Provider 缓存写入前完成。任何结构或引用错误都会使本次生成失败，不会产生 Feature Guide 草案或缓存。

## 6. 检索和新鲜度

草案保存后可通过以下 MCP 工具读取：

- `knowledge_search`：Feature Guide 具有明确类型加权，并支持中文标题包含匹配；
- `knowledge_get`：返回完整草案、来源、可信度和新鲜度；
- `knowledge_context`：对匹配的功能开发任务优先选择 Feature Guide；
- `knowledge_impact`：从来源文件或符号查看受影响模块、测试和知识。

`draft` 即使处于 `fresh` 也会返回 `requires_live_source: true`。来源文件变化后，未重新生成的草案会变为 `potentially_stale`；来源删除后会变为 `stale`，不得作为单一修改依据。

## 7. 大项目使用建议

不要把一万个文件一次性交给模型。先用候选发现、`knowledge_context` 和 `knowledge_impact` 缩小到一个功能域，再选择入口、核心服务、配置和测试组成 EvidencePack。默认上限为 20 个文件和约 12000 Token，超限内容会明确列入 `omitted`。

对于 Lua/Skynet 项目，当前通用静态解析的证据质量仍有限。应先完成 WP-02 的 require、service、protocol、消息派发和 main 入口适配，再依照同一 Feature Guide Schema 生成语义草案；不要让模型猜测动态运行时关系。

## 8. 当前边界

- 系统能验证“引用存在且未被替换”，不能纯静态证明一句自然语言与引用在语义上完全等价；人工审核仍然必要。
- 当前没有把草案提升为 curated/verified 的命令；该能力属于 WP-05。
- 当前没有任务分类、多跳功能影响、参考实现推荐和可选向量检索；这些属于 WP-06。
- 生产 Provider 选择、允许外发的源码范围和组织级 Secret 策略仍需项目负责人明确决策。
