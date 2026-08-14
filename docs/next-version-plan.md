# 版本交付与后续计划：0.1.26 → 0.1.27

> 本文是当前交付状态和下一批开发的唯一计划入口。历史版本计划只保留在 Git 历史中。
> 复核日期：2026-08-14

## 0.1.26：WP-10 交付与验证可靠性

本批次先解决阻断交付、验证和本地运行环境的问题，不扩展业务能力。

| 需求 ID | 交付内容 | 验收证据 | 状态 |
| --- | --- | --- | --- |
| P0-CI-001 | 修复 GitHub Actions 质量工作流的折叠命令缩进，并加入无依赖结构校验 | `.github/workflows/quality.yml`；`tests/test_delivery_reliability.py` | 已完成 |
| P0-EVAL-001 | 禁止失败评测报告作为回归基线；报告记录生成时间、提交、包版本和源码快照哈希 | `evaluation/reports/latest.json`；40 题全策略评测；质量门输出 | 已完成 |
| P0-DOC-001 | 审计报告和本计划反映 0.1.26 当前状态、已知缺陷和后续边界 | `docs/project-knowledge-system-audit.md`；本文 | 已完成 |
| P0-ENV-001 | 使用仓库独立 `.venv`；`doctor` 报告实际包文件与期望源码路径 | `ProjectService.doctor()`；`python -m project_knowledge doctor` | 已完成 |
| P0-GIT-001 | 严格保留源码提交对齐；仅 PKS 生成产物提交可进入 `verification_aligned` | `tests/test_integration.py` 提交边界正负用例 | 已完成 |

### 0.1.26 验收标准与质量门

- `python -m unittest discover -s tests -v` 全部通过；
- `python -m project_knowledge --version` 输出 `0.1.26`；
- `evaluation/reports/latest.json` 的 `quality_gate.passed` 为 `true`，并显式记录 `project_commit`、`index_commit`、`working_tree`、源码快照哈希和包版本；
- CI 使用通过的历史基线，不使用曾失败的 `self-repo-0.1.8.json`；
- 生成知识完成同步；陈旧人工知识必须显式列为待复核，不能伪造为已验证。

## 0.1.27：评测和检索质量

0.1.27 只在 0.1.26 质量门通过后开始，工作包暂定如下：

1. 以 40 题自有评测和 `evaluation/questions-wp01-wp02.jsonl` 为回归集，修复 Markdown/混合检索的真实召回缺口；
2. 为不变量、设计原因和任务分类补充正负样本，保持最低阈值不变；
3. 复核当前 7 条陈旧人工知识，逐条确认、更新或保留待复核状态；
4. 对真实 Lua/Skynet 项目补充负责人确认的业务答案评测；
5. 继续保持 CodeGraph Adapter 未实现时的显式不可用，不以 builtin 结果冒充。

### 0.1.27 暂不承诺

- 生产级外部 Model Provider；
- 自建常驻 daemon 或跨客户端共享服务；
- 自动替用户确认 Feature Guide 或人工架构知识；
- 通过降低评测阈值解决质量门失败。

## 更长期方向

在连续两个版本拥有可复现的通过基线后，再讨论真正的 CodeGraph Adapter、真实客户端端到端矩阵、性能优化和团队协作治理。每项都必须先有真实样本、正负测试、文档和验收证据。
