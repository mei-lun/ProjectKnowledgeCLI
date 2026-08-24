# Project Knowledge CLI

## 当前质量指标（0.1.48）

当前真实 CodeGraph Adapter 已接入并可用：`codegraph-public-cli 1.5.0`。当前活动评测使用 50 个 self-repo 样本；精确指标和环境相关延迟以 [活动评测报告](evaluation/reports/latest.json) 为唯一来源，避免在 README 中复制会随 live 检索发生小幅波动的数据。

Adapter 可用不等于检索质量门已通过：当前 self-repo 活动质量门尚未通过，主要缺陷仍是候选覆盖、符号召回和排序精确率不足。Builtin engine 不属于当前运行时，也不会作为质量问题的回退方案。详细边界见 [当前 CodeGraph 评测状态](docs/codegraph-evaluation-current.md)。

## gardenserver 受控实践结果

`gardenserver` 是本轮检索优化的真实外部实践项目。Phase 0 与 Phase 1 使用同一稳定源码快照进行前后对照；结果只用于判断当前实现能否进入受控项目实践，不冒充跨仓库生产门。

| 指标 | Phase 0 | Phase 1 | 当前结论 |
| --- | ---: | ---: | --- |
| 文件、核心文件、符号召回率 | 1.00 | 1.00 | 保持完整召回 |
| precision@5 | 0.45 | 0.50 | Phase 1 达到原方案阶段门槛 |
| nDCG@5 | 0.938488 | 0.832547 | 两阶段均高于 0.80 门槛 |
| hybrid P95（优化前） | 19.93 s | 16.67 s | 基线 |
| hybrid P95（优化后） | 4.26 s | 4.48 s | 明显下降，仍未达到 P95 < 1.5 s |

当前结论是：检索召回、Top-5 相关性和排序质量已达到 gardenserver 的受控项目实践标准，可以接入真实开发任务观察；专用 Phase 1 质量门唯一仍失败的项目是 P95 延迟。正式生产规模验证仍按审计文档单独推进，不影响此次受控实践结论。

冻结证据见 [Phase 0 报告](evaluation/reports/gardenserver-phase0-0.1.46.json)、[Phase 1 报告](evaluation/reports/gardenserver-phase1-0.1.46.json) 和 [Phase 1 门槛](evaluation/thresholds-gardenserver-phase1.json)。完整实现范围与未完成项见 [检索质量工作包](docs/retrieval-quality-work-package.md)。

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
- npm 安装路径要求 Node.js 20 或更高版本、npm 10 或更高版本。
- Git，用于识别仓库、分支和提交状态。
- npm 安装包自带并固定使用 `@colbymchenry/codegraph@1.5.0`；源码开发需要另行提供可调用的 CodeGraph 公共 CLI。
- 默认本地运行，不要求联网，不发送遥测。

## 安装

当前 npm 首发验证范围是 Windows 10/11 x64。安装前确认本机命令满足最低版本：

```powershell
node --version       # 20+
npm --version        # 10+
py -3.11 --version   # 3.11+
git --version
```

### 从 npm registry 安装

包发布到 npm registry 后，全局安装只需要一条命令：

```powershell
npm install --global project-kb-cli
project-kb --version
```

如果 registry 返回 `E404`，表示该包或版本尚未公开发布。不要安装名称相近的第三方包，可等待正式发布，或按下一节从本仓库构建 tarball。

### 从本仓库 tarball 安装

维护者或发布前验收可以从干净源码构建同样的 npm 制品。以下命令在仓库根目录执行：

```powershell
.venv\Scripts\python.exe scripts\build_npm_package.py
Push-Location dist\npm-package
$package = npm pack --silent
npm install --global ".\$package"
Pop-Location
project-kb --version
```

npm 包会发现 Python 3.11+，在 `%LOCALAPPDATA%\ProjectKnowledgeCLI\runtimes\<版本>` 创建版本隔离的托管虚拟环境，并离线安装包内同版本 Python wheel。它还会使用包内固定的 `@colbymchenry/codegraph@1.5.0`。可以用 `PROJECT_KB_PYTHON` 指定 Python，或用 `PROJECT_KB_RUNTIME_HOME` 改变托管运行时根目录。

## 在项目中初始化

进入需要接入 Codex 的 Git 项目根目录，然后初始化并检查状态：

```powershell
Set-Location D:\path\to\your-repository
project-kb init
project-kb status . --json
project-kb doctor . --json
```

`init` 会初始化 CodeGraph 和项目知识库，并在保留用户原有内容的前提下写入工具拥有的 `AGENTS.md` 与 `.codex/config.toml` 标记块。重复执行是幂等的。

初始化完成后：

1. 在 Codex 中关闭并重新打开该项目，或重启 Codex。
2. 信任该项目，使 Codex 加载项目级 `.codex/config.toml`。
3. 在 Codex 的 MCP 列表中确认 `project_knowledge` 已启用。
4. 新建任务并要求 Codex 先调用 `knowledge_status` 和 `knowledge_context`；跨模块修改前调用 `knowledge_impact`。

这些 `knowledge_*` 名称是 Codex 调用的 MCP 工具，不是 PowerShell 命令。若它们在已经打开的旧任务中不可见，通常是因为该任务启动时尚未加载新 MCP 配置；重新打开项目并新建任务即可。

## 日常使用

代码发生变化后同步索引；需要完整重建或健康检查时使用对应命令：

```powershell
project-kb sync .
project-kb status . --json
project-kb check . --json
project-kb rebuild .
```

升级 npm 包后，需要在每个已初始化项目根目录再次运行 `init`，让 Codex 配置切换到新版本托管运行时：

```powershell
npm install --global project-kb-cli@latest
project-kb init
```

移除某个项目的 Codex/AGENTS 集成时先在该项目根目录执行 `uninstall`；它会保留 `.project-kb` 知识数据。随后可选择卸载全局 npm 包：

```powershell
project-kb uninstall
npm uninstall --global project-kb-cli
```

### 常见问题

- `project-kb` 找不到：重新打开终端，并确认 npm 全局 bin 目录在 `PATH` 中。
- Python 探测失败：安装 Python 3.11+，或设置 `PROJECT_KB_PYTHON` 为解释器绝对路径后重新安装/运行。
- `knowledge_status` 等工具不可见：确认项目已受信任、`.codex/config.toml` 存在，然后重启 Codex 并新建任务。
- 同名 MCP 配置冲突：`init` 不会覆盖用户自有的 `[mcp_servers.project_knowledge]`；先改名或迁移原配置再重试。
- 诊断安装：在目标项目根目录运行 `project-kb doctor . --json`，并用 `project-kb status . --json` 检查索引状态。

## 源码开发

源码开发仍可在源码目录执行：

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

源码安装后应能执行：

```bash
project-kb --version
```

源码开发应使用仓库独立 `.venv`，避免多个 Git 工作树的 editable install 相互覆盖。`doctor.package_source` 会在检查 PKS 源码仓库本身时报告实际导入位置是否与当前工作树一致。普通目标项目显示为 `external_project`，不会误报源码工作树冲突。

## 快速开始

也可以显式传入项目路径，而不切换当前目录：

```bash
project-kb init D:\path\to\repository
project-kb status D:\path\to\repository
project-kb check D:\path\to\repository
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
| `.codex/config.toml` | Codex 项目级 stdio MCP 配置；只维护 `project-kb:codex-mcp` 区块 | PKS 管理 |
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
