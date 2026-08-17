# 0.1.29 WP-12A 检索精确率设计

日期：2026-08-17  
目标版本：0.1.29  
工作包：WP-12A  
需求 ID：RT-010；补强 RT-006、RT-007、RT-008  
状态：设计已由用户确认，等待书面复核

## 1. 背景与结论

0.1.28 的 40 题自仓评测已经证明当前检索器具有较高文件召回，但候选结果仍不紧凑：

| 策略 | 文件召回 | 文件精确 | 平均返回文件 |
| --- | ---: | ---: | ---: |
| hybrid | 0.958333 | 0.161827 | 13.175 |
| code | 0.931250 | 0.202080 | 10.425 |
| Markdown | 0.902083 | 0.243750 | 8.000 |
| grep + Read | 0.689583 | 0.292083 | 5.225 |

每题平均仅有 2.2 个严格期望文件。当前 hybrid 会把直接符号、代码图影响、知识来源和 fallback 文件合并成最多 20 个无层级集合，因此“基本不漏”和“噪声较多”同时成立。`project-kb finalize --check` 的 `ready` 只代表交付证据对齐，不代表 RT-010 已达标。

本工作包采用确定性多信号重排，不引入网络、Embedding 或模型依赖。候选生成继续追求召回，统一重排层把结果拆成最多 5 个核心证据和受预算约束的补充证据。核心层优化精确率，完整集合继续守住召回。

## 2. 范围与非目标

### 2.1 范围

- 新增独立、离线、可解释的文件候选重排组件；
- 让 `KnowledgeAPI.context()` 返回有序的核心证据、补充证据和评分解释；
- 让 hybrid、code、Markdown 和 grep 评测策略复用生产重排逻辑；
- 增加核心文件精确率/召回率和 `nDCG@5` 诊断指标；
- 保持现有 40 题严格标注不变，并增加至少 10 个 hard-negative 样本；
- 提高精确率门槛，同时保留现有召回、Token、延迟和回归门。

### 2.2 非目标

- 不实现向量数据库、Embedding、LLM 重排或远程 Provider；
- 不通过缩小固定候选上限代替排序；
- 不修改现有 40 题的 `expected_files` 以制造指标提升；
- 不把可接受的补充文件计入严格核心精确率；
- 不改变 CodeGraph/builtin 的事实权威边界；
- 不在本工作包开放项目级评分权重配置；
- 不宣称动态派发、反射或运行时依赖注入可由静态重排确认。

## 3. 架构

### 3.1 数据流

```text
现有候选生成
  -> 候选规范化与去重
  -> 确定性多信号评分
  -> 稳定排序
  -> 核心证据（最多 5 个）
  -> 补充证据（完整集合最多 10 个，受最低分和 Token 约束）
  -> 兼容响应与评分解释
```

候选生成仍来自四类证据：

1. 直接符号和稳定 ID；
2. 代码图影响、调用关系和依赖路径；
3. curated/generated 知识记录的显式来源；
4. 路径与正文 fallback 检索。

重排器只消费结构化候选和任务特征，不直接访问 CLI、MCP 或评测数据。它不得读取标准答案，也不得根据策略名使用评测专用权重。

### 3.2 组件边界

新增 `src/project_knowledge/ranking.py`：

- `FileCandidate`：规范化候选及其证据集合；
- `ScoreBreakdown`：每类分值和总分；
- `RankedFile`：有序结果、层级、原因和状态；
- `RankingPolicy`：版本化默认权重、阈值和上限；
- `rank_files(...)`：纯确定性评分、排序和分层入口。

`src/project_knowledge/retrieval.py`：

- 继续负责任务分类、符号、影响和知识来源收集；
- 将所有候选转换为 `FileCandidate` 后调用重排器；
- 返回新增字段，并保持旧字段兼容。

`src/project_knowledge/evaluate.py`：

- 使用生产重排器，不再维护另一套文件相关性评分；
- 各基线策略只负责提供不同来源的候选，不自行决定最终顺序；
- 记录核心、完整集合、排序质量和降级状态。

## 4. 候选模型与规范化

`FileCandidate` 至少包含：

| 字段 | 含义 |
| --- | --- |
| `path` | 项目内规范化相对路径，作为去重键 |
| `stages` | `direct_symbol`、`impact`、`knowledge_source`、`fallback` 等来源集合 |
| `anchors` | 稳定符号 ID、知识记录 ID 或关系锚点集合 |
| `exact_symbol` | 是否存在精确稳定符号或完整限定名命中 |
| `qualified_symbol` | 是否存在前缀或限定符号命中 |
| `direct_knowledge_source` | 是否为已选知识记录的直接来源 |
| `graph_hop` | 与直接锚点的最短关系跳数；未知为 `null` |
| `module` | 候选所属模块 |
| `task_role_match` | 文件角色是否符合任务类型，例如测试任务命中测试文件 |
| `path_terms` | 路径中命中的唯一任务词 |
| `symbol_terms` | 候选符号中命中的唯一任务词 |
| `content_terms` | 正文中命中的唯一任务词 |
| `is_test` | 是否为测试文件 |
| `requires_live_source` | 是否需要实时源码复核 |

规范化阶段必须：

- 拒绝项目外路径、空路径和不存在的文件；
- 统一路径分隔符并消除重复候选；
- 合并同一路径的 stages、anchors 和最短图距离；
- 在排序前沿用现有 stale/pending 屏蔽，不能因重排重新暴露旧内容；
- 不把 generated 报告、冻结基线和被配置排除的路径重新加入候选。

## 5. 默认评分策略

首版使用版本化常量 `policy-v1`。同一类别只取最高适用档位，避免同一事实重复加分；不同类别可以累加。

评分类别固定为 identity、provenance、relation、role、text 和 penalties：前三行属于 identity，直接知识来源属于 provenance，图关系属于 relation，任务角色属于 role，路径/符号/正文词项属于 text，负分属于 penalties。identity 类只取最高档；relation 类只取最短 hop；text 类分别按表中上限累计；其他类别各应用一次。

| 信号 | 分值 |
| --- | ---: |
| 精确稳定符号、完整限定名或完整相对路径命中 | +100 |
| 前缀或限定符号命中 | +70 |
| 精确文件名或精确模块命中 | +40 |
| 直接知识来源 | +35 |
| 一跳代码图关系 | +30 |
| 二跳代码图关系 | +12 |
| 任务类型与文件角色匹配 | +20 |
| 路径唯一任务词 | 每个 +8，最多 +24 |
| 符号唯一任务词 | 每个 +6，最多 +18 |
| 正文唯一任务词 | 每个 +2，最多 +8 |
| 无关测试文件 | -25 |
| 仅有宽泛正文 fallback、无其他证据 | -15 |
| 需要实时复核 | 不降排序分；保留显式状态 |

“无关测试文件”是指任务没有测试意图、候选也不是直接符号命中、直接知识来源或一跳受影响测试。受影响测试不能仅因文件角色被降权。

排序键固定为：

```text
(-total_score, -highest_stage_priority, normalized_path)
```

stage 优先级固定为：`direct_symbol > knowledge_source > impact > fallback`。相同索引、任务和配置必须产生完全一致的顺序、分数和解释。

## 6. 核心与补充证据

### 6.1 核心证据

- 最多 5 个；
- 默认要求总分不低于 30；
- 没有候选达到 30 时，仍返回最高分的 1 个候选，并标记 `ranking_confidence=low`；
- 精确符号候选优先进入核心层；
- 核心层先占用上下文预算，不能被补充证据挤出。

### 6.2 补充证据

- `files` 完整集合最多 10 个；
- 补充候选默认要求总分不低于 12；
- 精确符号、直接知识来源和一跳影响属于受保护候选，即使达到普通上限也必须优先保留在完整集合中；
- 若受保护候选超过 10 个，仍按统一排序只保留前 10 个，并返回 `protected_candidates_truncated=true`；
- Token 预算不足时从补充层尾部裁剪，不能裁剪核心层；
- 被裁剪的候选记录在 `withheld_files` 及其原因中，不计入 `files`。

### 6.3 兼容响应

`KnowledgeAPI.context()` 新增：

```json
{
  "core_files": ["src/example.py"],
  "supporting_files": ["tests/test_example.py"],
  "files": ["src/example.py", "tests/test_example.py"],
  "file_rankings": [
    {
      "path": "src/example.py",
      "tier": "core",
      "score": 100,
      "score_breakdown": {"identity": 100},
      "selection_stage": "direct_symbol",
      "why_selected": "精确稳定符号命中。"
    }
  ],
  "ranking_policy": "policy-v1",
  "ranking_status": "ok"
}
```

`core_files`、`supporting_files`、`files`、`file_rankings` 和 ranking 状态都是 `KnowledgeAPI.context()` 的新增字段；`files` 是前两层的有序并集。现有 `symbols`、`knowledge`、`impact`、`reference_implementations` 等字段含义不变，现有客户端忽略新增字段后仍可工作。评测报告中的 `returned_files` 继续表示完整并集。

## 7. 降级与错误处理

- 单项评分信号不可用时该项计零，并在 `score_breakdown.unavailable_signals` 中记录；
- 不存在、越界或被排除的路径在规范化阶段丢弃，并记录结构化原因；
- 重排器发生未预期异常时，运行时保留规范化后的原候选顺序，返回 `ranking_status=fallback` 和无敏感信息的原因码；
- 任何正式评测样本出现 `ranking_status=fallback`，该策略质量门失败；
- CodeGraph 不可用继续使用现有 `adapter_unavailable` 语义，不得把 builtin 关系伪装为 CodeGraph；
- 重排解释不得包含绝对路径、环境变量、密钥或未脱敏 Provider 内容。

## 8. 评测与防止指标注水

### 8.1 数据集规则

- 保持现有 40 题及其 `expected_files` 不变；
- 新增至少 10 个 hard-negative 样本，覆盖：
  - 同名或相似符号；
  - 无关测试文件；
  - 宽泛词频命中；
  - 二跳及更远依赖；
  - 知识页过度引用；
- 可以新增人工确认的 `acceptable_supporting_files`，但只生成辅助诊断指标；
- 严格核心精确率只认可 `expected_files`；
- 数据集、阈值和基线继续记录并比较 SHA-256，哈希不一致不得做相对回归比较。

### 8.2 新指标

- `core_file_precision`：核心文件命中数 / 实际核心文件数；
- `core_file_recall`：核心文件命中数 / 严格期望文件数；
- `file_precision`、`file_recall`：继续衡量完整有序并集；
- `ndcg_at_5`：按 `expected_files` 二元相关性计算，首版作为诊断指标；
- `average_returned_files`、`average_core_files`：监控候选紧凑度；
- `ranking_fallback_rate`：正式质量门要求为 0。

所有指标继续逐题计算后取宏平均。报告必须同时保存样本数、逐题结果、排序解释和失败指标。

### 8.3 目标质量门

| 策略 | 指标 | 最低值或最高值 |
| --- | --- | ---: |
| hybrid | `core_file_recall` | >= 0.85 |
| hybrid | `core_file_precision` | >= 0.40 |
| hybrid | `file_recall` | >= 0.94 |
| hybrid | `file_precision` | >= 0.22 |
| hybrid | `average_returned_files` | <= 10 |
| hybrid | `average_context_tokens` | <= 1000 |
| code | `file_recall` | >= 0.92 |
| code | `file_precision` | >= 0.25 |
| Markdown | `file_recall` | >= 0.90 |
| Markdown | `file_precision` | >= 0.30 |
| grep + Read | `file_recall` | >= 0.67 |
| grep + Read | `file_precision` | >= 0.32 |
| 全部可用策略 | `ranking_fallback_rate` | = 0 |

现有召回、符号、调用路径、扩展点、不变量、设计原因、成功率、Token、工具调用和延迟门继续生效。不得为了达到新精确率目标而降低旧阈值。

## 9. 测试策略

实施必须先增加失败测试和评测样本，再修改行为。

### 9.1 单元测试

- 每个正向信号和负向信号的独立分值；
- 同类信号只取最高档、跨类信号正确累加；
- 路径规范化、去重、stage/anchor 合并和最短 hop；
- 同分稳定排序；
- 核心阈值、补充阈值、上限、受保护候选和 Token 裁剪；
- 低置信度单候选与重排异常 fallback。

### 9.2 负向测试

- 同名测试符号不能挤出精确生产符号；
- 宽泛正文高词频不能压过稳定 ID；
- 二跳依赖不能压过直接知识来源或一跳关系；
- 重复知识来源不能重复加分；
- stale/pending、项目外路径和排除目录不能重新出现；
- fallback 响应不得泄露绝对路径或异常详情。

### 9.3 集成和评测测试

- `KnowledgeAPI.context()` 新旧字段兼容和有序并集；
- builtin 与真实 CodeGraph 夹具产生统一候选契约；
- hybrid、code、Markdown 和 grep 复用同一重排器；
- 40 题原集、至少 10 题 hard-negative 集和合并正式集；
- 相同数据集重复运行的顺序、分数和解释完全一致；
- 质量门对低精确率、召回回退、候选过多和 ranking fallback 返回退出码 2。

## 10. 文档、版本与知识同步

- 本设计属于 WP-12A 同一开发批次，设计中间提交不单独递增版本；
- 实现交付前运行一次 `python scripts/bump_version.py "提高检索精确率并增加核心证据重排"`；
- 唯一版本源从 0.1.28 递增到 0.1.29，CHANGELOG 同步记录；
- `docs/project-knowledge-system-audit.md` 只有在全部验收证据通过后才能把 RT-010 标为已完成；
- 更新 `evaluation/questions.jsonl`、`evaluation/thresholds.json`、冻结基线和版本化报告；
- 复核 `curated.architecture`、`curated.conventions` 和 `curated.feature.guide.generation`；无法确认的内容保持待复核，不能伪造 verified；
- 源码和文档提交后运行 `project-kb finalize`，审核并提交生成物，再以 `project-kb finalize --check` 验证 `ready`。

## 11. 验证命令

至少运行：

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m project_knowledge evaluate evaluation\questions.jsonl --project . --strategy all --thresholds evaluation\thresholds.json --baseline evaluation\baselines\self-repo-0.1.29.json --output evaluation\reports\latest.json --quiet
.venv\Scripts\python.exe scripts\validate_codegraph_adapter.py
.venv\Scripts\python.exe -m project_knowledge --version
.venv\Scripts\python.exe -m project_knowledge finalize . --check --json
```

正式冻结新基线前，先运行不带新基线的绝对质量门评测；只有干净工作区、相同数据集哈希和全部绝对门通过后，才能写入 `self-repo-0.1.29.json`。

## 12. 完成定义

只有同时满足以下条件，WP-12A 和 RT-010 才能标记为完成：

- `ranking.py` 的生产重排器已接入 `KnowledgeAPI.context()`；
- 评测不再维护独立的文件排序实现；
- 核心/补充证据、评分拆解、兼容字段和结构化降级均有正负测试；
- 现有 40 题未改答案，并增加至少 10 个 hard-negative 样本；
- 所有新旧质量门和同数据集回归门通过；
- builtin 和真实 CodeGraph 验证均通过；
- 全量测试通过，版本和 CHANGELOG 一致；
- 审计、评测报告、冻结基线和相关 curated knowledge 已复核；
- 生成知识已同步，最终 `project-kb finalize --check` 返回 `ready`。
