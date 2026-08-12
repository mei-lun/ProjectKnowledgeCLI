# Project Knowledge CLI

PKS 是本地优先的项目级知识库：复用 CodeGraph 获取代码事实，维护可重建索引，并生成带来源的中文项目知识与类别级开发指导。

| 项目 | 当前状态 |
| --- | --- |
| 版本 | `0.1.21` |
| 环境 | Python 3.11+；默认本地、禁网、无遥测 |
| 代码事实 | CodeGraph 1.5 公共 CLI/API，不读私有数据库 |
| 知识目录 | PKS 产物统一在 `.project-kb/` |
| 验证项目 | gardenserver：1,295 文件，0 解析错误 |

## 已完成能力

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 初始化、重建 | 已完成 | `init/rebuild` 建库并原子替换索引 |
| 增量和自动同步 | 已完成基础版 | `sync/watch` 处理修改、新增和删除 |
| 健康与新鲜度 | 已完成 | pending、stale、提交对齐、watcher 健康 |
| CodeGraph Adapter | 已完成 | init、sync、查询、调用链、影响和测试影响接口 |
| Lua/Skynet/zn 证据 | 已完成首版 | require、启动、RPC、消息、Avatar、配置规则 |
| 两层开发指导 | 已完成首版 | 可迁移方法论 + gardenserver 项目适配 |
| 中文指导 | 已完成 | 普通活动、普通玩家功能、登录模块 |
| MCP | 已完成基础版 | context、search、get、impact、status |
| 来源追踪 | 已完成基础版 | 文件、符号、行号和哈希，新鲜度可见 |
| 语义草案与审核 | 已完成基础版 | EvidencePack、Feature Guide、Proposal、ADR 草案 |
| 版本控制 | 已完成 | 单一版本源、补丁版本和中文 CHANGELOG |

## 名称由来

| 名称 | 含义 | 说明 |
| --- | --- | --- |
| PKS | Project Knowledge System | 项目知识系统的产品简称 |
| `project-kb` | Project Knowledge Base | CLI 命令名；`kb` 是 Knowledge Base（知识库）的缩写 |
| `.project-kb.yml` | Project Knowledge Base configuration | 项目根目录的配置文件 |
| `.project-kb/` | Project Knowledge Base data | PKS 在项目中的本地数据与知识目录；前导点表示工具管理目录 |

## 目录结构与用途

### 项目根目录

| 路径 | 用途 | 所有权/维护建议 |
| --- | --- | --- |
| `.project-kb.yml` | 项目名、索引引擎、扫描范围、知识路径、更新和 Provider 策略 | 用户配置；PKS 初始化创建，后续可人工维护 |
| `.project-kb/` | 集中存放 PKS 的索引、状态、证据和知识产物 | PKS 主目录；不要整体手工删除，重建前先备份人工知识 |
| `.codegraph/` | CodeGraph 1.5 的数据库和运行时文件 | CodeGraph 管理；不是 PKS 私有数据库，PKS 只调用公开 CLI |

### `.project-kb/` 根文件

| 路径 | 用途 | 所有权/维护建议 |
| --- | --- | --- |
| `index.db` | 文件、符号、关系和 KnowledgeRecord 的 SQLite 兼容索引 | PKS 自动重建，不手工编辑 |
| `manifest.json` | 文件、知识、来源哈希、新鲜度和生成元数据清单 | PKS 自动更新，不手工编辑 |
| `index.md` | 自动、草案、人工和决策知识的中文索引 | PKS 自动生成 |
| `mcp.json` | 当前项目的只读 MCP 启动配置 | PKS 安装/初始化维护 |
| `state.json` | watcher 状态、PID、heartbeat、错误和协调信息 | PKS 运行时维护 |

### `.project-kb/` 子目录

| 路径 | 用途 | 所有权/维护建议 |
| --- | --- | --- |
| `generated/` | 项目地图、入口、测试地图和三类中文开发指导 | PKS 自动覆盖，不手工修改 |
| `evidence/` | CodeGraph 和源码规则采集的结构化事实证据 | PKS 自动生成；排查指导来源时读取 |
| `methodology/` | 第一层“可迁移方法论”的结构化 JSON | PKS 管理；未来需通过审核治理提升 |
| `guides/` | 第二层“当前项目适配”的结构化 JSON | PKS 随代码同步更新 |
| `drafts/` | 模型或工具生成、尚未人工确认的语义草案 | PKS 写入；审核后通过 Proposal 提升 |
| `curated/` | 人工维护和已审核的项目知识 | 用户/团队维护；PKS 不静默覆盖人工正文 |
| `decisions/` | ADR 等架构决策文档 | 用户/团队审核；PKS 只允许受控追加草案 |
| `events/` | 每次同步产生的 ChangeSet 事件 | PKS 自动写入，用于追踪代码和知识影响 |
| `proposals/` | 可审核的知识更新提案及状态 | PKS 创建，人工 apply/reject |
| `proposals/queue/` | 代码变化触发的语义更新等待队列 | PKS 自动维护，等待生成或关联 Proposal |
| `logs/` | watcher、指导刷新和服务错误日志 | PKS 自动追加，故障排查使用 |
| `schemas/` | KnowledgeRecord、ChangeSet、Proposal 等 JSON Schema | PKS 随版本生成，不手工编辑 |
| `state/` | 锁、租约或后续运行状态辅助文件的目录 | PKS 运行时使用；当前主要为预留与兼容 |
| `codegraph/` | PKS 为 CodeGraph 适配预留的兼容目录 | 当前 CodeGraph 1.5 实际仍使用项目根 `.codegraph/` |

### 目录维护约定

> 当前采用人工维护方案。新增、删除、改名目录，或改变目录职责时，必须在同一批变更中同步更新本节、版本号和 CHANGELOG。现阶段没有自动检查 README 是否遗漏新目录。

| 变更 | 必须同步处理 |
| --- | --- |
| 新增目录或根文件 | 在对应表格新增路径、用途和所有权 |
| 删除或改名 | 更新表格、配置示例、测试和迁移说明 |
| 职责变化 | 更新用途、允许人工修改范围和失败恢复建议 |
| CodeGraph 目录规则变化 | 同时更新 `.codegraph/` 与 `.project-kb/codegraph/` 说明 |

## 生成内容

| 路径 | 内容 |
| --- | --- |
| `.project-kb/index.db` | PKS 兼容索引和知识记录 |
| `.project-kb/manifest.json` | 来源、新鲜度和知识清单 |
| `.project-kb/generated/项目地图.md` | 项目结构概览 |
| `.project-kb/generated/开发指导索引.md` | 指导入口 |
| `.project-kb/generated/普通活动开发.md` | 活动类两层指导 |
| `.project-kb/generated/普通玩家功能开发.md` | 玩家功能两层指导 |
| `.project-kb/generated/登录模块开发.md` | 登录类两层指导 |
| `.codegraph/` | CodeGraph 1.5 要求的运行时索引 |

## gardenserver 实测

| PKS 文件/符号/关系 | 解析错误 | CodeGraph 文件/节点/边 | 待同步 |
| ---: | ---: | ---: | ---: |
| 1,295 / 11,101 / 54,857 | 0 | 1,296 / 17,550 / 44,169 | 0 |

## 快速开始

当前版本只提供已有的本地索引兼容流程和五个只读知识工具：

```bash
python -m pip install -e .
project-kb init /path/to/repository
project-kb status /path/to/repository
project-kb mcp --project /path/to/repository
```

0.1.22 计划中的“CodeGraph 更新事实，MCP AI 客户端自动发现类别、生成可审核指导并确认入库”尚未实现，不能把上述命令理解为新开发指导工作流已经可用。`watch` 仍为旧兼容命令，但不再是推荐流程，也不属于下一版本架构。

## 常用入口

| 命令 | 当前用途 |
| --- | --- |
| `init / sync / rebuild` | 旧版兼容的本地索引建库与显式同步 |
| `status / check / doctor` | 查看当前索引与知识状态 |
| `mcp` | 启动当前五个只读知识工具 |
| `watch` | 旧版 PKS 文件监听兼容命令；新架构不使用 |
| `generate / feature-candidates` | 旧版语义草案和候选域流程 |
| `propose / apply / reject` | 旧版受控知识提案流程 |

## 当前限制

| 限制 | 后续 |
| --- | --- |
| 三类指导尚未注册为 KnowledgeRecord，MCP 召回未验收 | `0.1.22` P0 |
| 指导刷新目前只对 gardenserver 启用 | `0.1.22` P0 |
| 第二层仍偏事实罗列，步骤/不变量/测试/回滚不足 | `0.1.22` P0 |
| `watch` 属于旧兼容流程，不是下一版本的自动更新方案 | 由 CodeGraph 更新事实，AI 客户端在 MCP 参与时增量处理 |
| CodeGraph 必须使用独立 `.codegraph/` | 上游 1.5 限制 |
| Lua 动态调用可能漏边 | 未来运行时证据 |

## 版本管理

唯一版本源为 `src/project_knowledge/__init__.py`；同一批修改只递增一次：

```bash
python3 scripts/bump_version.py "本次变更的中文说明"
```

## 后续开发依据

| 文档 | 用途 |
| --- | --- |
| [下一版本计划](docs/next-version-plan.md) | `0.1.22` 确定范围和验收 |
| [通用指导设计规格](docs/superpowers/specs/2026-08-12-ai-client-development-guidance-design.md) | MCP AI 客户端、两阶段审核与增量分级设计 |
| [未来特性](docs/future-features.md) | 非承诺候选能力和前置条件 |
| [需求审计](docs/project-knowledge-system-audit.md) | 当前复核与历史追踪 |
| [系统设计](docs/project-knowledge-system-design.md) | 架构和生命周期 |
