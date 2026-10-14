# 当前 CodeGraph 评测状态（0.1.66）

复核日期：2026-08-19

2026-10-15 目录整理复核：活动报告 `evaluation/reports/latest.json` 的 `package_version` 为 `0.1.62`，与当前包版本不一致。本次保留原始报告，未重跑完整检索评测；以下 Adapter 和质量记录仍需结合报告来源及后续复测判断。

## 当前事实

- `project-kb status`：`engine=codegraph`、`adapter=codegraph-public-cli`、`adapter_version=1.5.0`、`available=true`。
- `create_engine()` 只创建 `CodeGraphEngine`；运行时不再导出或回退到 `BuiltinCodeIndexEngine`、Python/Lua/Generic parser。
- 真实 Adapter 验证已通过 `init/files/query/trace/impact/affected` 六项检查。
- `evaluation/reports/latest.json` 保留 CodeGraph 策略的历史测量；报告元数据为 `available=true`、`adapter=codegraph-public-cli`，但其包版本尚未与当前源码对齐。

## 历史报告边界

旧版 `latest.json`、`evaluation/baselines/self-repo-0.1.26.json`、`self-repo-0.1.28.json` 以及早期审计章节中的 `adapter_unavailable`、`builtin` 只表示当时的历史状态，不能作为当前运行时结论。当前活动报告由 `scripts/validate_evaluation_provenance.py` 校验。

## 质量结论

当前 CodeGraph Adapter 已接入且可用；历史精确检索指标和 P95 延迟以活动 JSON 报告的版本和测量结果为准，不在状态文档中复制 live 数值。冻结质量门仍未通过，这是 self-repo 检索质量问题，不是 Adapter 不可用，也不应通过恢复 Builtin fallback 或降低阈值解决。

CI 会拒绝以下活动报告：包版本过期、CodeGraph 不可用、非 `codegraph-public-cli` Adapter、`adapter_unavailable` 元数据或 Builtin engine 元数据。
