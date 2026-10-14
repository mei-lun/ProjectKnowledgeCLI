# Project Knowledge CLI

Project Knowledge CLI（PKS）是建立在 CodeGraph 之上的本地项目知识层：CodeGraph 提供文件、符号、调用关系和影响范围，PKS 负责知识记录、来源哈希、新鲜度、检索和可审核的 AI 开发指导。

PKS 不替代 CodeGraph，也不读取 CodeGraph 私有数据库；运行时只使用 CodeGraph 公共 CLI/API。

## 先看结论

- 当前版本：`0.1.66`
- 当前已验证平台：Windows 10/11 x64、Node.js 20+、npm 10+、Python 3.11+
- 默认本地运行，不发送遥测；默认不需要网络
- Codex 集成是项目级的；Pi 可额外安装用户级扩展
- npm registry 尚未公开可用时，请使用本仓库构建的 tarball，不要把 `E404` 当成可安装状态

## 安装

### 推荐路径：从本仓库构建并安装 tarball

以下命令在本仓库根目录执行。它会构建同版本 Python wheel，组装 npm 包，并安装到全局 npm 前缀。

```powershell
node --version       # 20+
npm --version        # 10+
py -3.11 --version   # 3.11+
git --version

py -3.11 scripts\build_npm_package.py
Push-Location dist\npm-package
$package = npm pack --silent
npm install --global ".\$package"
Pop-Location

project-kb --version
```

安装器会在 `%LOCALAPPDATA%\ProjectKnowledgeCLI\runtimes\<版本>` 创建版本隔离的托管 Python 环境，并离线安装包内同版本 wheel。需要自定义解释器或运行时位置时，可设置：

```powershell
$env:PROJECT_KB_PYTHON = "C:\Python312\python.exe"
$env:PROJECT_KB_RUNTIME_HOME = "D:\ProjectKnowledgeCLI\runtimes"
```

### npm registry 安装

只有在包已经发布到 registry 后才使用这条路径：

```powershell
npm install --global project-kb-cli
project-kb --version
```

如果返回 `E404`，说明当前包或版本尚未公开发布。此时使用上一节的 tarball 安装；不要安装名称相近的第三方包。

## 接入 Codex 和 Pi

### Pi：用户级扩展

安装 npm 包后，在任意目录执行：

```powershell
project-kb agent install --global --target pi
project-kb doctor --global --json
```

默认写入 `%USERPROFILE%\.pi\agent\extensions\project-kb.ts`。如需改目录，先设置 `PI_CODING_AGENT_DIR`。安装后重启 Pi。

Codex 不通过这个全局命令注册。Codex 使用下面的项目级初始化流程，避免跨电脑复制绝对路径后失效。

### Codex：项目级 MCP

进入目标 Git 项目根目录：

```powershell
Set-Location D:\path\to\your-repository
project-kb init
project-kb status . --json
project-kb doctor . --verify-mcp --json
```

`init` 会初始化 CodeGraph 和 `.project-kb`，并只更新 PKS 自己拥有的 `AGENTS.md`、`.codex/config.toml` 和 Git hook 标记块；用户已有内容会保留，重复执行幂等。

完成后重启 Codex，并在新任务中确认 `project_knowledge` MCP 已加载。常用只读工具包括：

- `knowledge_status`：索引、Git、知识新鲜度和指导工作流状态
- `knowledge_context`：按任务组织核心、支持和可选证据
- `knowledge_search`：搜索 generated、draft、curated 和 decision 知识
- `knowledge_get`：读取单条知识记录
- `knowledge_impact`：查询文件、符号和依赖影响范围

首次初始化会按批次覆盖全项目，并生成分类、方法论和项目事实指导草稿。草稿必须由用户审核后才会成为正式知识；未审核草稿不会冒充 `verified` 结果。

## 快速开始

已经安装 CLI 后，一个新项目的最短接入流程是：

```powershell
Set-Location D:\path\to\your-repository
project-kb init
project-kb doctor . --verify-mcp --json
```

然后重启 Codex 并新建任务。Codex 会通过 MCP 读取项目状态与任务上下文；首次建库需要先审核分类目录，再分别审核方法论和项目事实指导。

## 常用入口

| 命令 | 用途 |
| --- | --- |
| `project-kb init` | 初始化 CodeGraph、知识库和项目级客户端集成 |
| `project-kb status . --json` | 查看索引、新鲜度和待处理动作 |
| `project-kb sync .` | 同步源码及生成知识 |
| `project-kb doctor . --verify-mcp --json` | 验证环境、配置与 MCP 查询 |
| `project-kb uninstall` | 移除当前项目的 PKS 集成并保留知识数据 |

## 日常操作

所有命令都可以在项目根目录执行，也可以显式传入项目路径。

```powershell
# 查看状态和健康门
project-kb status . --json
project-kb check . --json

# 同步新增、修改和删除
project-kb sync . --task-summary "描述本次修改"

# 需要时执行一次完整原子重建
project-kb rebuild .

# 前台轮询同步；只运行一轮时使用 --once
project-kb watch . --once
```

代码变化后，先 `sync`，再查看 `status`。如果状态显示 `pending_files`、`reconciliation_required` 或 `stale_knowledge`，先按返回的下一动作处理，不要把旧知识当作当前事实。

交付前检查发布边界：

```powershell
project-kb finalize . --json
project-kb finalize . --check --json
```

`finalize --check` 是只读检查，不会执行 `git add`、`git commit` 或 `git push`。

## 安装、升级和卸载

### 升级 CLI

```powershell
npm install --global project-kb-cli@latest
project-kb --version
project-kb agent install --global --target pi
Set-Location D:\path\to\your-repository
project-kb init
```

如果 registry 尚未发布新版本，则按“推荐路径”重新构建 tarball。升级后在每个项目根目录重新运行 `init`，让项目 MCP 配置指向当前安装的启动器。

### 移除项目集成，保留知识数据

```powershell
Set-Location D:\path\to\your-repository
project-kb uninstall --dry-run
project-kb uninstall
```

这只移除 PKS 自己写入的项目级客户端标记和 Git hooks；`.project-kb`、`.codegraph`、用户配置和非 PKS hook 内容会保留。也可以用 `--client codex`、`--client claude`、`--client cursor` 或 `--client gemini` 只移除对应集成。Pi 用户级扩展不属于这个命令的范围。

### 移除全局 CLI 和 Pi 扩展

```powershell
project-kb agent uninstall --global --target pi
npm uninstall --global project-kb-cli
```

### 彻底删除当前项目数据

这是不可逆的数据删除操作。确认当前目录确实是目标项目根目录，并提前备份要保留的知识后执行：

```powershell
Set-Location D:\path\to\your-repository
project-kb uninstall
Remove-Item -LiteralPath .project-kb -Recurse -Force
Remove-Item -LiteralPath .codegraph -Recurse -Force
Remove-Item -LiteralPath .project-kb.yml -Force
```

上述命令不会删除整个项目，也不会删除 `AGENTS.md`、`.codex/config.toml` 或 Git hooks 中的非 PKS 内容。

## MCP 审计日志

审计默认关闭。需要记录当前项目的 MCP 调用时：

```powershell
project-kb mcp-log enable --project . --json
# 重启 MCP 连接并执行调用
project-kb mcp-log status --project . --json
project-kb mcp-log validate --project . --json
project-kb mcp-log export --project . --output analysis.jsonl --json
project-kb mcp-log evaluate --analysis analysis.jsonl --ground-truth labels.jsonl --json
project-kb mcp-log disable --project . --json
```

日志写入 `.project-kb/logs/mcp-events.jsonl`，包含 JSON-RPC、工具调用、耗时、检索阶段和依赖调用；认证信息、Token、密码、Cookie 和私钥会脱敏。`disable` 不删除历史日志。

## 当前质量指标（0.1.66）

当前真实 CodeGraph Adapter 为 `codegraph-public-cli 1.5.0`。Adapter 可用不等于所有检索质量门已通过；当前 self-repo 活动质量门尚未通过，详细数据以 [活动评测报告](evaluation/reports/latest.json) 为准。当前实现不回退到 builtin engine。

2026-10-15 目录整理复核：活动报告的 `package_version` 仍为 `0.1.62`，尚未与当前版本对齐；该报告不能作为当前版本已通过完整评测的证据。

## gardenserver 受控实践结果

`gardenserver` 结果只用于受控项目实践，不冒充跨仓库生产质量门：

| 指标 | Phase 0 | Phase 1 | 当前结论 |
| --- | ---: | ---: | --- |
| 文件、核心文件、符号召回率 | 1.00 | 1.00 | 保持完整召回 |
| precision@5 | 0.45 | 0.50 | Phase 1 达到阶段门槛 |
| nDCG@5 | 0.938488 | 0.832547 | 两阶段均高于 0.80 门槛 |
| hybrid P95（优化前） | 19.93 s | 16.67 s | 基线 |
| hybrid P95（优化后） | 4.26 s | 4.48 s | 仍未达到 P95 < 1.5 s |

召回和排序已达到受控实践标准，但正式生产规模验证仍在继续。证据见 [Phase 0 报告](evaluation/reports/gardenserver-phase0-0.1.46.json)、[Phase 1 报告](evaluation/reports/gardenserver-phase1-0.1.46.json) 和 [检索质量工作包](docs/retrieval-quality-work-package.md)。

## 常见问题

- `project-kb` 找不到：重新打开终端，并确认 npm 全局 bin 目录在 `PATH` 中。
- Python 探测失败：安装 Python 3.11+，或设置 `PROJECT_KB_PYTHON` 后重新安装。
- `knowledge_status` 等工具不可见：确认项目已受信任、`.codex/config.toml` 存在，然后重启 Codex 并新建任务。
- Codex MCP 验证失败：在项目实际目录运行 `project-kb doctor . --verify-mcp --json`；不要只依赖 `codex -C` 的配置发现结果。
- Pi 工具不可见：检查 `%USERPROFILE%\.pi\agent\extensions\project-kb.ts`，重新运行 Pi 安装命令并重启 Pi。
- 同名 MCP 配置冲突：PKS 不覆盖用户自有的 `[mcp_servers.project_knowledge]`，请先迁移或改名原配置。

## 源码开发

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m project_knowledge --version
.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test npm/test/agent-installer.test.js npm/test/runtime.test.js
```

常用验证脚本：

```powershell
.venv\Scripts\python.exe scripts\validate_codegraph_adapter.py --json
.venv\Scripts\python.exe scripts\validate_npm_bootstrap.py --json
.venv\Scripts\python.exe scripts\validate_mcp_observability.py
```

完整参数以命令帮助为准：

```powershell
project-kb --help
project-kb <command> --help
```

## 项目文件

本仓库按用途保留以下目录：

| 路径 | 用途 |
| --- | --- |
| `src/project_knowledge/` | Python CLI、MCP 和知识工作流源码 |
| `tests/` | Python 回归测试 |
| `npm/` | Node 启动器、安装模板和测试 |
| `plugins/` | Codex 插件、技能与任务同步入口 |
| `scripts/` | 版本、构建和验收脚本 |
| `evaluation/` | 评测题集、冻结基线和正式结果 |
| `docs/` | 使用说明、设计、审计及项目知识 |
| `.github/` | 持续集成质量检查 |

`build/`、`dist/`、`.tmp-dist*/`、`__pycache__/` 和 `.pytest_cache/` 是可再生成的构建或测试产物，不纳入版本管理。清理时保留 `.venv/` 开发环境、正在使用的知识索引和未审核草稿；Git 工作树须先检查未提交文件和分支合入状态。历史评测只清理无引用的中间试跑结果，冻结基线、正式报告及审计证据保留。

以下是 PKS 管理的项目数据；本仓库通过 `.project-kb.yml` 将 generated、curated 和 decisions 配置在 `docs/knowledge/` 下：

| 路径 | 用途 | 维护者 |
| --- | --- | --- |
| `.project-kb.yml` | 项目配置、扫描范围和知识路径 | 用户/PKS |
| `.project-kb/index.db` | CodeGraph 快照、知识和指导状态 | PKS |
| `.project-kb/generated/` | 自动生成的项目地图、模块和测试知识 | PKS |
| `.project-kb/drafts/` | 等待审核的分类、方法论和指导草稿 | PKS/用户审核 |
| `.project-kb/curated/` | 人工维护的正式知识 | 用户 |
| `.project-kb/decisions/` | 架构决策记录 | 用户 |
| `.codegraph/` | CodeGraph 运行时索引 | CodeGraph |
| `.codex/config.toml` | 项目级 Codex MCP 配置块 | PKS 自有块 |

## 版本管理

唯一版本源是 `src/project_knowledge/__init__.py`。每批源码或文档变更只递增一次补丁版本：

```powershell
python scripts/bump_version.py "中文变更说明"
python -m project_knowledge --version
```

变更记录写入 [CHANGELOG.md](CHANGELOG.md)。

## 参考文档

| 文档 | 用途 |
| --- | --- |
| [兼容性矩阵](docs/compatibility-matrix.md) | 运行环境、配置、客户端和引擎边界 |
| [系统设计](docs/project-knowledge-system-design.md) | 架构、存储和知识生命周期 |
| [需求审计](docs/project-knowledge-system-audit.md) | 需求、验收证据和历史复核记录 |
| [评测指南](docs/evaluation-guide.md) | 检索质量数据集和评测方法 |
| [当前 CodeGraph 评测状态](docs/codegraph-evaluation-current.md) | Adapter 与质量门现状 |
