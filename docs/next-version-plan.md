# 版本交付与后续计划：0.1.27 → 0.1.28

> 本文是当前交付状态和下一批开发的唯一计划入口。历史版本计划只保留在 Git 历史中。
> 复核日期：2026-08-14

## 0.1.27：WP-11 交付闭环、CodeGraph 主链路与检索精度

本批次解决三个已确认问题：发布同步缺少确定性闭环、真实 CodeGraph Adapter 未进入查询主链路，以及高召回伴随过量无关文件。实现、正负测试、评测和真实适配器夹具均已完成，最终交付以本节质量门为准。

| 需求 ID | 交付内容 | 验收证据 | 状态 |
| --- | --- | --- | --- |
| REL-001～REL-005 | 新增只读检查与可写同步分离的 `project-kb finalize` 状态机；只接受生成物提交作为最终对齐边界，不执行 Git 写操作 | `tests/test_finalization.py`、`.github/workflows/quality.yml` | 已完成 |
| CG-001～CG-004 | 统一 CodeGraph 1.5 公共 CLI 的初始化、文件、符号、追踪、影响和受影响测试契约；不可用时明确失败且不回退 builtin | `tests/test_codegraph.py`、`tests/test_retrieval_wp06.py` | 已完成 |
| CG-005 | 使用临时四文件项目运行真实 CodeGraph CLI，覆盖 `init/files/query/trace/impact/affected` | `scripts/validate_codegraph_adapter.py`、`tests/test_codegraph_validation.py` | 已完成 |
| CG-006 | `engine=codegraph` 时，`KnowledgeAPI` 即使在 SQLite 没有符号或关系缓存，也从 CodeGraph 获取实时结构事实 | `tests/test_retrieval_wp06.py`、动态 codegraph 评测策略 | 已完成 |
| RET-001～RET-005 | 修正评测锚点；按知识页引用、直接命中、依赖关系分阶段选证据；限制核心文件并返回选择理由 | `tests/test_evaluate.py`、`tests/test_retrieval_wp06.py` | 已完成 |
| RET-006 | 在不降低既有召回门槛的前提下冻结 0.1.27 精确率门槛 | `evaluation/thresholds.json`、`evaluation/reports/latest.json` | 已完成 |

### 0.1.27 验收标准与质量门

- `python -m unittest discover -s tests -v` 全部通过；
- `python -m project_knowledge --version` 输出 `0.1.27`，且 `CHANGELOG.md` 有对应记录；
- `project-kb evaluate ... --fail-on-regression` 对 40 题全部策略通过，hybrid 召回率不低于 `0.90`、精确率不低于 `0.12`，code 精确率不低于 `0.20`，Markdown 精确率不低于 `0.12`；
- `python scripts/validate_codegraph_adapter.py --command <真实 CodeGraph CLI>` 的六项检查全部通过，且不在源码仓库创建 `.codegraph`；
- 源码与文档提交后执行 `project-kb finalize . --json`，提交其生成物，再执行 `project-kb finalize . --check --json`；
- 生成知识已同步，人工知识的来源变更均经过人工复核，不把 `potentially_stale` 伪装为 `fresh`。

## 0.1.28：WP-12 真实项目采用与质量扩展

0.1.28 只接收可验证、可回退的增量，不扩大 CodeGraph 私有边界。

| 候选需求 ID | 计划 | 验收方向 |
| --- | --- | --- |
| CG-PROJ-001 | 为真实项目提供显式启用、初始化诊断和迁移说明 | 至少两个临时镜像夹具覆盖成功、未初始化、CLI 不可用和路径含空格场景 |
| RET-QUAL-001 | 扩展跨语言和真实业务问题集，并分别报告查询类型指标 | 新问题先补 ground truth；保持 0.1.27 冻结门槛，不用降低阈值换取通过 |
| DOC-GOV-001 | 将人工知识来源复核纳入发布检查和审核记录 | 能列出、确认或拒绝每个待复核记录，审计结果可追溯 |
| PERF-001 | 建立 CodeGraph 实时查询和混合检索的延迟、缓存与退化基线 | 固定夹具、分位数、冷/热查询和失败路径均有自动化报告 |

### 0.1.28 验收标准

- 每项进入开发前必须在审计报告中登记需求 ID、正负样本和回退边界；
- 真实项目验证只使用临时只读镜像，不能污染源项目；
- 性能或精度优化不得牺牲事实来源、不可用状态可见性和 0.1.27 冻结质量门；
- 实现、测试、评测、文档、版本和知识同步全部完成后，才能将需求标记为已完成。

## 更长期方向

- 多仓库和跨服务知识关联；
- 运行时 trace、日志和发布事件与静态事实的受控融合；
- 面向团队的知识审核、权限和签名；
- 更大规模代码库的分片索引、增量物化和资源预算治理。

这些条目是方向，不代表开发承诺。只有迁入当前版本计划并补齐需求 ID 与验收样本后，才进入实施。
