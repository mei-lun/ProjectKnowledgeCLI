# Project Knowledge CLI

PKS 是本地优先的项目级知识库：复用 CodeGraph 获取代码事实，维护可重建索引，并生成带来源的中文项目知识与类别级开发指导。

| 项目 | 当前状态 |
| --- | --- |
| 版本 | `0.1.17` |
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

```bash
python -m pip install -e .
project-kb init /path/to/repository
project-kb status /path/to/repository
project-kb watch /path/to/repository
project-kb mcp --project /path/to/repository
```

## 常用入口

| 命令 | 用途 |
| --- | --- |
| `init / sync / rebuild / watch` | 建库与更新 |
| `status / check / doctor` | 状态与诊断 |
| `mcp` | 启动五个只读知识工具 |
| `generate / feature-candidates` | 生成语义草案和候选域 |
| `propose / apply / reject` | 受控审核知识更新 |

## 当前限制

| 限制 | 后续 |
| --- | --- |
| 三类指导尚未注册为 KnowledgeRecord，MCP 召回未验收 | `0.1.18` P0 |
| 指导刷新目前只对 gardenserver 启用 | `0.1.18` P1 |
| 第二层仍偏事实罗列，步骤/不变量/测试/回滚不足 | `0.1.18` P1 |
| watcher 大项目轮询较慢 | 未来性能候选 |
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
| [下一版本计划](docs/next-version-plan.md) | `0.1.18` 确定范围和验收 |
| [未来特性](docs/future-features.md) | 非承诺候选能力和前置条件 |
| [需求审计](docs/project-knowledge-system-audit.md) | 当前复核与历史追踪 |
| [系统设计](docs/project-knowledge-system-design.md) | 架构和生命周期 |
