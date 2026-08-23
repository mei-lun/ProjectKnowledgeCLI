# 项目知识库评测指南

## 目标

评测用于回答“知识库是否真的改善功能开发”，不用于用未经验证的平均值宣传效果。快速集固定为 `evaluation/questions.jsonl`，当前包含 40 条中文开发问题和实时源码校验过的文件、符号、调用路径、扩展点、不变量或设计原因锚点；其中 5 条覆盖 Provider、EvidencePack、授权、dry-run 和扩展点。

## 策略

- `hybrid`：当前知识、符号和影响分析组合。
- `grep_read`：词法检索后读取最多八个文件，作为 grep + Read 对照。
- `code`：仅使用符号与关系结果，不使用 Markdown 正文。
- `markdown`：仅使用知识检索与知识正文，不使用代码符号搜索。
- `codegraph`：只允许真实 CodeGraph Adapter。Adapter 不存在时报告 `adapter_unavailable`，不得回退到 builtin 伪造结果。

`markdown` 仍以知识检索和知识正文为答案来源；代码符号与依赖锚点只用于重排知识记录已经声明的来源路径，来源文件最多返回 8 个。这样不会把代码正文伪装成 Markdown 答案，同时避免一个模块记录把数十个无关来源全部展开。

## CodeGraph 公开 CLI 验证

运行以下命令会创建临时 Python/Lua 项目，并通过公开 CLI 验证 `init/files/query/trace/impact/affected`；验证结束后默认删除夹具，不会在当前源仓库创建 `.codegraph`：

```bash
python scripts/validate_codegraph_adapter.py --json
```

退出码仅在六项检查全部通过时为 `0`。报告包含 CLI 来源、Adapter 版本、各阶段耗时和归一化结果数量；排障时可加 `--keep-fixture`，检查完成后需手动删除报告中的临时目录。

## 指标

报告按适用样本分别计算文件、符号、调用路径、扩展点、不变量和设计原因的召回率；结构锚点同时计算精确率。成本指标包括上下文 Token、工具调用次数、P50/P95/P99 延迟和最终成功率。报告还记录数据集哈希、项目提交、索引提交、引擎能力、Python、平台、处理器和 CPU 数。

空期望项不进入该指标分母，避免把“本题不要求符号”错误计算为符号召回失败。样本只有在其所有适用期望项均命中时才计为成功。

## Required evidence（Dataset v2）

需要验收裁剪完整性的样本可声明 `schema_version: 2` 和独立评测 Oracle
`required_evidence`。其中 `symbols` 保存稳定符号 ID 及可选的
`signature`/`span`，`relation_paths` 保存连续的有序边序列
`{source, kind, target}`。Oracle 只由评测器读取，不会传入运行时查询，避免用标签反向驱动被测规划器。

运行时在召回和排序完成后自行生成 `pre_required_evidence`，预算装配后生成
`post_required_evidence`。评测分别报告规划召回、裁剪后符号/签名/行号保留、完整有序路径保留，
以及 `context_incomplete` 与真实缺失集合的一致性；端点集合不能替代路径指标。

评测器对代码策略同时保留两层结果：`pre_budget_*` 来自预算装配前的排名诊断，
用于衡量候选发现和排序；`files`、`core_files` 与 `file_rankings` 来自最终预算后的上下文，
用于衡量实际交付内容。预算裁剪可以移除排名诊断字段，但不得因此让评测器把缺失字段当成零召回，
也不得用预算前排名冒充最终上下文。

当 `minimum_required_tokens <= max_tokens` 时，required evidence 的保留门槛为 1.0；
预算不可行时必须遵守硬 Token 上限，并返回 `context_incomplete=true`、
`missing_required_evidence` 和 `budget_status=insufficient_for_required`，不得静默删除后报告完整。

上下文响应还包含 `context_status`：`state` 取 `complete`、`low_confidence`、
`needs_source_check` 或 `context_incomplete`，并同时给出 `confidence`、
`needs_source_check` 和可审计 `reasons`。状态按不完整优先级覆盖：预算导致 required 缺失时不能被高相关性或高排序分数掩盖；
无精确符号锚点、索引待同步、陈旧/推断知识、动态关系限制和 fallback 排序都会显式要求源码复核。

## 快速质量门

```bash
project-kb evaluate evaluation/questions.jsonl \
  --project . \
  --strategy all \
  --thresholds evaluation/thresholds.json \
  --baseline evaluation/baselines/self-repo-0.1.28.json \
  --output evaluation/reports/latest.json \
  --json
```

质量门失败返回退出码 `2`。阈值按策略独立冻结，避免要求 grep+Read 返回代码图符号，或把 Markdown 的高 Token 成本隐藏在混合平均值里。

0.1.3 首次冻结前先排除了 `evaluation/reports/**` 和 `evaluation/baselines/**`，防止历史答案进入下一轮被测索引。稳定轮次实测 grep+Read 文件召回率/精确率为 `0.675/0.29375`、only-Markdown 文件精确率为 `0.087313`，对应门槛以小幅跨环境余量冻结为 `0.67/0.29/0.085`。这组数值是当前能力下限，不是产品目标；冻结后的任何降低都必须有新证据、版本记录和人工复核。

0.1.4 将快速集扩展到 25 题，最低样本数同步提升为 25，未降低任何指标阈值。基线比较只有在 `dataset_sha256` 相同时才执行；数据集变化时报告 `baseline_dataset_mismatch` 并只检查绝对阈值，避免比较不可比的汇总均值。only-Markdown 每题最多读取三页，并在总 Token 预算内抽取与任务相关的片段。

0.1.28 基线使用当前 40 题数据集在干净工作区生成，CI 校验器会同时验证基线文件存在且 `dataset_sha256` 与题集一致；不允许继续引用只能执行绝对门、无法进行可比回归的旧基线。

## 性能评测

```bash
PYTHONPATH=src python evaluation/performance_harness.py \
  --sizes 500 5000 \
  --repetitions 5 \
  --output evaluation/reports/performance.json
```

夹具固定使用 `src/benchmark/file_N.py`，报告初始化、空同步、状态、上下文的延迟分位数，并在源码变更后验证旧生成正文确实被屏蔽。环境元数据必须随报告保存，不允许跨硬件直接比较绝对耗时。

## 真实 Lua/Skynet 项目

`evaluation/lua-skynet-ground-truth-candidates.md` 保存业务问题候选。根据 D-003，未经授权不得向目标目录写入；根据 D-007，业务标准答案必须由指定负责人确认。未确认问题只能标为候选，不能进入冻结质量阈值。

## CI

GitHub Actions 在 push 和 pull request 中运行单元测试、初始化快速集并执行质量门；每周计划任务额外运行 500/5000 文件性能评测。完整报告作为构建产物保留，便于审计失败样本。

0.1.8 保持快速集为 40 题，并另设 5 题 WP-01/WP-02 补充集，新增任务分类、解释型评分、有界多跳影响、参考实现、扩展点和 unknowns 问题；冻结基线为 `evaluation/baselines/self-repo-0.1.8.json`，未降低既有策略阈值。
