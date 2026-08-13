# 下一功能版本计划：0.1.22

> 本文是 0.1.22 的实现与验收基线；实现代码已在 `codex/ai-guidance-workflow` 工作树完成，gardenserver 的独立草稿仍等待用户审核。完整设计见[设计规格](superpowers/specs/2026-08-12-ai-client-development-guidance-design.md)。

## 版本目标

由接入 MCP 的 AI 客户端基于 CodeGraph 事实自动发现功能类别，通过可点击中文 Markdown 与用户完成两阶段审核，将确认后的两层开发指导写入 KnowledgeStore；代码变化后仅处理变化代码及必要影响范围。

## 需求清单

| ID | 优先级 | 功能 | 核心验收 | 估算 |
| --- | --- | --- | --- | ---: |
| NV-MODEL-001 | P0 | 通用类别、指导和审核状态模型 | 无项目名和三类示例硬编码；正式状态与历史进入 KnowledgeStore | 1～2 人日 |
| NV-INIT-001 | P0 | 首次全项目分批初始化 | 全项目覆盖、分批分析、简单断点继续、覆盖率可见 | 2～3 人日 |
| NV-MCP-001 | P0 | MCP AI 客户端工作流 | 分析、提交、草稿、哈希确认和查询闭环 | 2～3 人日 |
| NV-INCR-001 | P0 | CodeGraph 增量与三级更新 | 后续不重扫源码；一级自动、二级指导审核、三级分类审核 | 2～3 人日 |
| NV-VERIFY-001 | P0 | 通用测试与真实项目验收 | 临时项目集成测试和 gardenserver 真实审核通过 | 2～3 人日 |

预计总工作量：9～14 人日。

## 验收标准与核心约束

| 项目 | 已确认规则 |
| --- | --- |
| AI 位置 | 分析和沟通由 MCP AI 客户端负责，PKS 不调用内置 ModelProvider |
| 事实来源 | CodeGraph 公共 CLI/API；PKS 不重写 CodeGraph |
| 首次初始化 | 全项目覆盖、分层分批、简单断点继续 |
| 审核顺序 | 先确认分类目录，再分别审核轻量方法论与项目事实指导；两者互不隐式确认 |
| 审核载体 | 必须生成可点击中文 Markdown，不能只在聊天中描述 |
| 正式来源 | KnowledgeStore 唯一正式；Markdown 是审核和阅读投影 |
| 生成位置 | 新文件全部直接位于目标项目 .project-kb 根目录 |
| 自动更新 | CodeGraph 更新事实；下次 MCP AI 客户端参与时处理变化 |
| 增量范围 | 可比较全项目元数据，但只分析变化代码和必要上下文 |
| 更新分级 | 一级自动；二级指导和三级分类必须审核 |
| 确认安全 | 草稿 ID + 正文哈希；入库正文等于用户审核正文 |
| 失败安全 | 不覆盖正式指导、不推进快照、不丢失变化 |

## MCP 接口

新增 knowledge_initialization_start、knowledge_initialization_next、knowledge_initialization_submit、knowledge_draft_save、knowledge_draft_confirm、knowledge_changes、knowledge_update_submit。

扩展现有 knowledge_status/context/search/get/impact，返回覆盖率、待处理变化、正式指导新鲜度和待审核文件路径。

## 实施顺序

| 顺序 | 需求 | 完成标志 |
| ---: | --- | --- |
| 1 | NV-MODEL-001 | Schema、状态流转、KnowledgeStore 和哈希规则测试通过 |
| 2 | NV-INIT-001 | 临时项目可分批扫描、恢复并生成分类草稿 |
| 3 | NV-MCP-001 | AI 客户端可完成两阶段审核并精确入库 |
| 4 | NV-INCR-001 | 增删改代码触发正确等级且不重扫源码 |
| 5 | NV-VERIFY-001 | 临时项目与 gardenserver 真实增量验证通过；最终正式入库仍以用户审核为准 |

每项先补正负测试或评测样本，再实现行为。字段、配置、空接口或静态样例不能作为完成证据。

## 明确不进入 0.1.22

- PKS 自建 watcher、代码图或索引；
- 内置或联网 ModelProvider；
- 并行扫描、后台队列、复杂自动重试；
- Web 审核界面和自动确认；
- 跨项目知识中心、向量检索、Lua 运行时跟踪；
- 旧 .project-kb 子目录的全面迁移或删除。

## 完成定义

- 设计规格第 12 节全部满足；
- 全量自动化测试通过；
- gardenserver 只用于验证，不修改业务源码；
- README、审计、CHANGELOG、版本和知识同步；
- 明确报告 generated knowledge 是否同步以及 curated/ADR 是否需要复核。

长期候选见[未来特性](future-features.md)，不得直接从候选清单开始开发。
