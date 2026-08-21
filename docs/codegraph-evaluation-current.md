# 当前 CodeGraph 评测状态（0.1.41）

复核日期：2026-08-19

## 当前事实

- `project-kb status`：`engine=codegraph`、`adapter=codegraph-public-cli`、`adapter_version=1.5.0`、`available=true`。
- `create_engine()` 只创建 `CodeGraphEngine`；运行时不再导出或回退到 `BuiltinCodeIndexEngine`、Python/Lua/Generic parser。
- 真实 Adapter 验证已通过 `init/files/query/trace/impact/affected` 六项检查。
- `evaluation/reports/latest.json` 已按 0.1.41 重新对齐，覆盖 50 题 CodeGraph 策略，报告元数据为 `available=true`、`adapter=codegraph-public-cli`。

## 历史报告边界

旧版 `latest.json`、`evaluation/baselines/self-repo-0.1.26.json`、`self-repo-0.1.28.json` 以及早期审计章节中的 `adapter_unavailable`、`builtin` 只表示当时的历史状态，不能作为当前运行时结论。当前活动报告由 `scripts/validate_evaluation_provenance.py` 校验。

## 质量结论

当前 CodeGraph Adapter 已接入且可用；0.1.41 活动报告沿用同一已验证源码快照的文件召回率 `0.798333`、核心文件精确率为 `0.324667`、符号召回率为 `0.183333`、符号精确率为 `0.127381`、成功率为 `0.08`、P95 延迟为 `5860.27 ms`。冻结质量门仍未通过，这是检索质量问题，不是 Adapter 不可用，也不应通过恢复 Builtin fallback 或降低阈值解决。

CI 会拒绝以下活动报告：包版本过期、CodeGraph 不可用、非 `codegraph-public-cli` Adapter、`adapter_unavailable` 元数据或 Builtin engine 元数据。
