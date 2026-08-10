# ADR-0002：CodeGraph Adapter 边界与本地替代方案

- 状态：草案
- 来源提案：kp-12a589734edb3c2d
- 创建审核人：codex

- 状态：已接受
- 日期：2026-08-07
- 版本：0.1.8
- 决策者：项目维护者

## 背景

审计要求 CodeGraph 与内置索引引擎具有相同公共契约，同时禁止在 CodeGraph 不可用时用 builtin 结果冒充。当前仓库没有可复现、可锁定版本的真实 CodeGraph 服务，也没有 Lua/Skynet 运行时图的稳定外部接口。

## 决策

1. CodeIndexEngine 的公共契约固定为 initialize、sync、search_symbols、get_source、trace、impact、affected_tests 和 status。
2. 0.1.8 起由 BuiltinCodeIndexEngine 作为默认、离线、可复现的正式替代实现；它提供 Python AST、Lua/Skynet 证据、SQL、配置和有界图查询，并在 status 中公开精确语言、保守语言、能力与限制。
3. engine: codegraph 不回退到 builtin，当前继续返回 adapter_unavailable/明确错误。只有在接入可验证的 CodeGraph 服务、版本锁定、同契约适配器测试和失败降级报告后，才允许实现该选项。
4. 业务开发指导必须把静态结构事实与运行时语义分开：动态分派、反射、依赖注入、Lua metatable、协议运行时名称必须标为待验证，不得生成确定性结论。

## 后续接入门槛

- 提供 CodeGraph 服务版本、连接方式、项目隔离和离线失败行为；
- 为 builtin 与 CodeGraph 编写同一组契约测试；
- 用 Lua/Skynet 代表性工程完成随机边精度、5 个主入口和影响分析对照；
- 在 doctor/status 中报告真实 Adapter 版本、能力和不可用原因；
- 通过 evaluation/thresholds.json 的冻结门槛，且不得降低已有 builtin 基线。

## 影响

该决策使当前系统可在无外部服务时稳定运行，同时明确不能把静态索引当作完整运行时知识。CodeGraph 后续属于可插拔增强，不影响现有项目知识库格式和 CLI。
