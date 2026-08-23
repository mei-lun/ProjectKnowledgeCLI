# Project Knowledge CLI

## 当前质量指标（0.1.43）

当前真实 CodeGraph Adapter 已接入并可用：`codegraph-public-cli 1.5.0`。以下指标来自当前活动评测报告 `evaluation/reports/latest.json`，评测集包含 50 个样本：

| 指标 | 当前值 |
| --- | ---: |
| 文件召回率 | 0.798333 |
| 文件精确率 | 0.302167 |
| 核心文件召回率 | 0.798333 |
| 核心文件精确率 | 0.324667 |
| 符号召回率 | 0.183333 |
| 符号精确率 | 0.127381 |
| 成功率 | 0.08 |
| 平均上下文 | 147.54 tokens |
| 平均工具调用 | 3 |
| P95 延迟 | 5860.27 ms |

Adapter 可用不等于检索质量门已通过：当前主要缺陷仍是候选覆盖、符号召回和排序精确率不足。Builtin engine 不属于当前运行时，也不会作为质量问题的回退方案。详细边界见 [当前 CodeGraph 评测状态](docs/codegraph-evaluation-current.md)，原始数据见 [活动评测报告](evaluation/reports/latest.json)。

Project Knowledge CLI（PKS）是本地优先的项目知识工具。它从代码索引中获取事实，维护带来源和新鲜度状态的知识记录，并通过 MCP 为 AI 客户端提供项目上下文、影响分析和可审核的开发指导。

## 核心能力

- 初始化、增量同步和原子重建项目知识索引。
- 查询项目上下文、知识记录、代码影响范围和健康状态。
- 标记知识来源、可信度、新鲜度以及需要回查源码的内容。
- 基于 CodeGraph 公共 CLI/API 获取文件、符号、调用关系和影响事实。
- 通过 MCP 完成功能分类、轻量方法论和项目事实指导的生成与审核。
- 将方法论和项目事实指导作为独立资产审核、版本化和查询。
- 在代码变化后只处理变化文件及必要的影响范围。

## 环境要求

- Python 3.11 或更高版本。
- Git，用于识别仓库、分支和提交状态。
- 如启用 CodeGraph 引擎，需要可调用的 CodeGraph 公共 CLI。
- 默认本地运行，不要求联网，不发送遥测。

## 安装

在源码目录执行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m project_knowledge doctor . --json
```

Linux/macOS：

```bash
. .venv/bin/activate
python -m pip install -e .
python -m project_knowledge doctor . --json
```

安装后应能执行：

```bash
project-kb --version
```

源码开发应使用仓库独立 `.venv`，避免多个 Git 工作树的 editable install 相互覆盖。`doctor.package_source` 会在检查 PKS 源码仓库本身时报告实际导入位置是否与当前工作树一致。普通目标项目显示为 `external_project`，不会误报源码工作树冲突。

如果终端找不到 `project-kb`，请使用虚拟环境中的 `python -m project_knowledge`，或激活该虚拟环境后再执行 `project-kb`。

## 快速开始

初始化项目并检查状态：

```bash
project-kb init /path/to/repository
project-kb status /path/to/repository
project-kb check /path/to/repository
```

启动 MCP 服务：

```bash
project-kb mcp --project /path/to/repository
```

接入 MCP 的 AI 客户端先读取状态和任务上下文，再基于 CodeGraph 事实分析功能分类。用户先审核分类目录，然后分别审核方法论和项目事实指导；两类资产不会相互隐式确认。

## 常用入口

| 命令 | 用途 |
| --- | --- |
| `init` | 初始化配置、索引、知识目录和 MCP 配置 |
| `sync` | 同步修改、新增、删除及知识变化 |
| `rebuild` | 原子重建本地索引并保留受控知识 |
| `status` | 查看索引、知识新鲜度、覆盖率和待处理事项 |
| `check` | 执行适合 CI 的健康检查 |
| `finalize` | 同步发布知识或以只读模式验证最终提交边界 |
| `doctor` | 检查 Python、SQLite、Git、引擎和项目配置 |
| `install` / `uninstall` | 安装或移除工具拥有的客户端集成标记 |
| `mcp` | 启动知识查询与开发指导工作流的 stdio MCP 服务 |
| `evaluate` | 对检索数据集执行质量评测 |

完整参数以命令帮助为准：

```bash
project-kb --help
project-kb <command> --help
```

交付源码和文档提交后，先同步发布知识；审阅并提交生成物后，再执行只读检查：

```bash
project-kb finalize /path/to/repository --json
project-kb finalize /path/to/repository --check --json
```

`finalize` 不会执行 `git add`、`git commit` 或 `git push`。如果仍有源码改动、待同步内容或待提交生成物，它会返回对应状态和下一步，而不会把未完成状态伪装成对齐。

## MCP 工作流

MCP 同时提供只读查询和受控写入工具：

- 查询：`knowledge_status`、`knowledge_context`、`knowledge_search`、`knowledge_get`、`knowledge_impact`。
- 初始化：分批读取稳定代码快照、提交候选分类并生成分类目录草稿。
- 审核：保存、拒绝或通过“草稿 ID + 正文哈希”确认 Markdown 草稿。
- 增量：比较已处理快照与当前 CodeGraph 快照，按事实、指导或分类级别提交更新。

KnowledgeStore 是正式知识来源；Markdown 是可阅读、可审核的投影。未经用户确认的草稿不会覆盖正式版本，也不会推进已处理快照。

## 项目文件

| 路径 | 用途 | 维护方式 |
| --- | --- | --- |
| `.project-kb.yml` | 项目、引擎、扫描范围和知识路径配置 | 用户配置 |
| `.project-kb/index.db` | 代码索引、KnowledgeRecord 和指导版本 | PKS 管理 |
| `.project-kb/manifest.json` | 文件、来源、新鲜度和生成元数据 | PKS 管理 |
| `.project-kb/mcp.json` | 项目 MCP 启动配置 | PKS 管理 |
| `.project-kb/generated/` | 项目地图、入口、测试地图等生成知识 | PKS 覆盖 |
| `.project-kb/curated/` | 人工维护并审核的项目知识 | 用户维护 |
| `.project-kb/decisions/` | 架构决策记录 | 用户审核 |
| `.project-kb/schemas/` | 运行时 JSON Schema | PKS 管理 |
| `.codegraph/` | CodeGraph 自身的运行时索引 | CodeGraph 管理 |

指导审核文件直接位于 `.project-kb/`：

- `<类别>-方法论-待审核.md` / `<类别>-方法论.md`
- `<类别>-项目事实指导-待审核.md` / `<类别>-项目事实指导.md`

## 安全与一致性

- PKS 只通过 CodeGraph 公共 CLI/API 获取代码事实，不读取其私有数据库。
- 正式知识写入使用 Schema 校验、来源哈希和事务。
- 人工维护的正文不会被生成流程静默覆盖。
- 动态调用、反射和运行时依赖注入可能无法由静态索引完整识别，相关结论需要回查源码或运行时证据。
- `.project-kb/` 与 `.codegraph/` 职责独立，不应手工混合或整体删除。

## 版本管理

唯一版本源是 `src/project_knowledge/__init__.py`。每批修改在交付前执行一次：

```bash
python scripts/bump_version.py "中文变更说明"
python -m project_knowledge --version
```

变更记录写入 [CHANGELOG.md](CHANGELOG.md)。

## 参考文档

| 文档 | 用途 |
| --- | --- |
| [兼容性矩阵](docs/compatibility-matrix.md) | 运行环境、配置、客户端和引擎兼容范围 |
| [系统设计](docs/project-knowledge-system-design.md) | 架构、存储和知识生命周期 |
| [需求审计](docs/project-knowledge-system-audit.md) | 需求、验收证据和历史复核记录 |
| [评测指南](docs/evaluation-guide.md) | 检索质量数据集与评测方法 |
