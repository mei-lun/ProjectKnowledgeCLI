# Project Knowledge npm 一键安装与 Codex 项目初始化设计

日期：2026-08-24
目标实施版本：0.1.48
状态：用户已批准方案与 Windows 范围，等待书面设计复核
工作包：WP-NPM-01

## 1. 背景与目标

Project Knowledge CLI 当前以 Python 包交付。用户必须创建虚拟环境、安装源码、定位 Python 可执行文件，再手工把 stdio MCP 注册到 Codex。仓库虽然已有 `project-kb init`、Codex Plugin 和 MCP 服务，但 `.project-kb/mcp.json` 不会被 Codex 自动读取，`project-kb` 也不一定存在于 Codex 进程的 `PATH` 中。

本工作包只改变安装与接入路径，不重写 Python Core、CodeGraph Adapter、知识存储、检索、MCP 工具或数据模型。目标用户路径为：

```powershell
npm install --global project-knowledge-cli
cd D:\path\to\repository
project-kb init
```

第二条命令完成知识库初始化、项目说明安装和 Codex 项目级 MCP 配置。用户随后只需重启 Codex 或新建任务。

## 2. 范围与非目标

### 2.1 本期范围

- 正式支持 Windows 10/11 x64、Node.js 20+、npm 10+、Python 3.11+。
- npm 包提供全局 `project-kb` 命令。
- npm 安装阶段或首次运行阶段创建 Project Knowledge 独立虚拟环境。
- npm 发布物携带与自身版本完全一致的 Python wheel。
- npm 包直接依赖并固定经验证的 CodeGraph 1.5 版本，启动 Python Core 时传入确定的 CodeGraph 命令。
- `project-kb init` 自动、安全、幂等地写入 `AGENTS.md` 和 `.codex/config.toml`。
- 从真实 `npm pack` 产物执行 Windows 原生端到端验证。

### 2.2 明确非目标

- 不把 Python Core 或 MCP 服务改写为 Node.js。
- 不自动下载或安装 Python；没有 Python 3.11+ 时给出单一、可执行的错误指引。
- 不在本工作包声明 Linux、macOS 或 WSL 已受支持。
- 不支持一次初始化后自动注册所有 AI 客户端。
- 不修改检索排序、知识 Schema、CodeGraph 公共接口或 MCP 工具参数。
- 不在 npm 卸载时遍历、修改或删除任何项目知识。
- 不把 npm registry 凭据或首次公开发布作为本地实现完成条件；发布前必须另行获得 registry 权限。

## 3. 需求与验收 ID

| ID | 要求 | 验收证据 |
| --- | --- | --- |
| NPM-001 | 全局 npm 安装后提供 `project-kb` | 隔离 npm prefix 中从 tarball 安装并执行 `project-kb --version` |
| NPM-002 | 自动发现 Python 3.11+，创建版本隔离环境并安装内置 wheel | 正向、无 Python、低版本 Python、损坏环境和重复运行测试 |
| NPM-003 | CLI 参数、stdin、stdout、stderr 和退出码保持现有契约 | Node 转发单元测试及 `--version`、失败命令、stdio MCP 测试 |
| NPM-004 | `init` 自动写入 AGENTS 和项目级 Codex MCP 配置 | 临时 Git 项目执行一次命令后检查两个所有权区块 |
| NPM-005 | 重复初始化幂等并保留用户配置 | 二次运行字节级稳定；标记外内容不变；同名非托管 MCP 配置明确失败 |
| NPM-006 | npm 产物可被 Codex 作为 stdio MCP 启动 | 通过生成配置启动进程，完成 `initialize` 和 `tools/list`，包含五个只读工具 |
| NPM-007 | npm、wheel、Plugin 和 CLI 版本来自唯一 Python 版本源 | 构建测试确认 staging `package.json`、wheel 元数据、Plugin 与 `__version__` 一致 |

只有实现、正负测试、真实 npm tarball 验证、文档、版本、审计和知识同步全部完成，才能在审计报告中把这些 ID 标记为完成。

## 4. 总体架构

```text
npm 全局包 project-knowledge-cli
  ├─ Node 启动器 project-kb
  ├─ Python wheel（构建时放入 vendor）
  └─ 固定版本 CodeGraph npm 依赖
             │
             ▼
  Windows 托管运行时目录
  %LOCALAPPDATA%\ProjectKnowledgeCLI\runtimes\<version>\
             │
             ▼
  <venv-python> -m project_knowledge <原始参数>
             │
             ├─ 现有 Python Core / SQLite / CodeGraph
             └─ 现有 stdio MCP 工具
```

Node 层只负责运行时准备、CodeGraph 命令定位和进程转发。业务初始化和项目文件所有权仍由 Python `ProjectService` 负责，避免在 Node 与 Python 中复制 AGENTS、配置和卸载规则。

## 5. npm 发布物

仓库新增 `npm/` 源目录，但不提交带正式版本号的发布 `package.json`。建议结构为：

```text
npm/
  package.template.json
  bin/project-kb.js
  lib/python-runtime.js
  lib/forward.js
  test/*.test.js
scripts/build_npm_package.py
```

`package.template.json` 不包含 `version`。`scripts/build_npm_package.py` 从 `src/project_knowledge/__init__.py` 读取唯一版本，构建 wheel，生成临时 staging `package.json`，复制 Node 文件和 wheel，然后在 staging 目录执行 `npm pack`。正式 npm 包名固定为 `project-knowledge-cli`，命令名固定为 `project-kb`，Node 引擎要求为 `>=20`。

npm 依赖固定到仓库已经验证的 `@colbymchenry/codegraph@1.5.0`。Node 启动器解析包内 CodeGraph CLI 的绝对路径，并通过 `CODEGRAPH_COMMAND` 传给 Python 进程；生成 Codex 配置时也保存该绝对路径，避免依赖 Codex 的 `PATH`。

## 6. 托管 Python 环境

### 6.1 Python 发现

候选顺序为：

1. 用户显式设置的 `PROJECT_KB_PYTHON`；
2. Windows Python Launcher：`py -3.11`；
3. `python`；
4. `python3`。

每个候选都通过子进程读取 `sys.version_info` 和 `sys.executable`，只接受 3.11 及以上版本。失败输出不得包含完整环境变量或其他敏感配置。

### 6.2 创建和复用

运行时目录为 `%LOCALAPPDATA%\ProjectKnowledgeCLI\runtimes\<npm-version>`；缺少 `LOCALAPPDATA` 时使用 `%USERPROFILE%\.project-kb\runtimes\<npm-version>`。准备流程使用同级临时目录，成功安装和校验后再原子移动到正式目录。

完整标记至少记录 npm 版本、Python 主次版本、wheel SHA-256 和成功时间。版本、哈希或 `python -m project_knowledge --version` 任一不匹配时，该环境不可复用。并发安装使用独占锁；陈旧锁可按记录的 PID 和年龄恢复，活跃锁只等待有限时间后失败。

安装使用 venv 自带 Python 和 pip，从 npm 包 `vendor/` 执行离线安装。当前 Python 包没有第三方依赖；构建流程仍须收集 wheel 声明的全部 Windows 依赖，否则 npm 构建失败，不能在用户机器上退回联网安装。

`postinstall` 尝试预热运行时；npm 禁用安装脚本时，首次 `project-kb` 调用执行同一套 `ensureRuntime`，所以正确性不依赖 `postinstall`。

## 7. CLI 转发契约

Node 启动器解析并准备托管 Python 后，执行：

```text
<venv-python> -m project_knowledge <用户原始参数>
```

普通 CLI 使用继承的 stdin/stdout/stderr。MCP 模式也不得解析、缓存或重写 JSON-RPC 字节流。Node 子进程退出码原样返回；进程启动错误映射为稳定的 npm 启动器错误码；Ctrl+C 和终止信号转发给 Python 子进程。

Node 层不增加与 Python CLI 同名的业务参数。`project-kb --version` 必须输出 Python Core 版本，不输出 npm 启动器自己的第二套版本文本。

## 8. `init` 与 Codex 项目配置

### 8.1 写入时机

Python `ProjectService.initialize` 在 CodeGraph 初始化和原子知识库重建成功后，才写入 Codex 集成。CodeGraph 或建库失败时，不得留下一个指向未初始化项目的 Codex MCP 配置。

成功后初始化流程：

1. 使用现有 HTML 所有权标记合并 `AGENTS.md`；
2. 创建 `.codex/`；
3. 使用 `# project-kb:codex-mcp:start/end` 所有权标记合并 `.codex/config.toml`；
4. 更新 `.project-kb/mcp.json`，保持其他客户端兼容；
5. 在结构化结果中返回 `codex_config`、`agents_updated` 和 `restart_required`。

`--dry-run` 只报告这些预期写入，不创建目录或文件。

### 8.2 Codex MCP 配置

托管区块写入：

```toml
# project-kb:codex-mcp:start
[mcp_servers.project_knowledge]
command = "<当前 sys.executable 的绝对路径>"
args = ["-m", "project_knowledge", "mcp", "--project", "."]
cwd = "<项目根目录绝对路径>"

[mcp_servers.project_knowledge.env]
CODEGRAPH_COMMAND = "<CodeGraph CLI 绝对路径>"
# project-kb:codex-mcp:end
```

字符串按 TOML 基本字符串规则转义。写入前后均使用 Python 3.11 `tomllib` 验证完整文件。所有权标记外内容保持原样。

若已有本工具标记，替换该区块。若没有标记但已有 `[mcp_servers.project_knowledge]` 或其子表，视为用户拥有的同名配置：`init` 返回 `codex_config_conflict`，不覆盖该文件，并且不能声称 Codex 初始化完成。用户删除、改名或显式迁移该配置后可重试。

### 8.3 更新与卸载

npm 每个版本使用独立 venv。npm 更新不会静默切换已经初始化的项目；用户在项目根目录重新运行 `project-kb init` 后，托管区块才更新到新版本 Python 路径。旧 venv 保留，避免正在运行的 Codex 进程被破坏。

`project-kb uninstall` 删除 AGENTS 和 Codex 的工具所有权区块，保留用户配置、`.project-kb` 知识与托管 Python 环境。npm 卸载不扫描项目，也不删除项目数据。

## 9. 失败与恢复

| 场景 | 行为 |
| --- | --- |
| Python 不存在或版本过低 | 安装器/首次运行失败，列出 Python 3.11+ 安装要求和 `PROJECT_KB_PYTHON` 覆盖方式 |
| venv 创建或 wheel 安装失败 | 删除本次临时目录，保留既有完整运行时，返回子步骤和日志位置 |
| npm 安装脚本被禁用 | 首次命令懒初始化，不降低功能 |
| CodeGraph CLI 缺失或版本不匹配 | 在 Python `doctor/init` 现有边界内明确失败，不回退本地解析器 |
| 项目不是 Git 仓库 | 保持现有 CLI 错误，不写 Codex 配置 |
| Codex TOML 无法解析 | 不写文件，返回路径和解析错误 |
| 同名非托管 MCP 配置存在 | 不覆盖，返回稳定冲突原因和修复说明 |
| 第二次 `init` | 索引按现有语义重建；两个集成区块内容不变时不产生文件差异 |

## 10. 测试与真实验证

按照仓库基线先增加测试，再实现行为。

### 10.1 Python 测试

- `init --dry-run` 报告 AGENTS 和 Codex 配置但不写文件；
- 成功初始化后两个区块存在，TOML 可解析；
- 用户 TOML、注释和 AGENTS 内容保留；
- 重复初始化幂等；
- 非托管同名配置拒绝覆盖；
- CodeGraph/建库失败时不写 Codex 区块；
- `uninstall` 只移除工具区块并保留知识；
- Windows 路径中的空格、反斜杠和非 ASCII 字符可正确启动。

### 10.2 Node 测试

- Python 候选顺序和版本过滤；
- 环境目录、锁、完整标记、损坏环境恢复；
- wheel 哈希和版本不一致时拒绝复用；
- 参数、stdio、退出码和中断信号转发；
- npm 禁用脚本后的首次运行；
- CodeGraph 绝对路径注入。

### 10.3 Windows 原生端到端测试

1. 构建 wheel 和 npm staging；
2. `npm pack`；
3. 在临时 npm prefix 中从 tarball 全局安装；
4. 在临时 Git 项目运行 `project-kb init`；
5. 验证 `.project-kb`、AGENTS 和 `.codex/config.toml`；
6. 使用生成配置的 command/env 启动 MCP；
7. 执行 `initialize`、`tools/list` 和 `knowledge_status`；
8. 再次运行 `init`，确认用户内容保留且托管配置无差异；
9. 执行 `uninstall`，确认知识保留。

随后运行仓库返回的 `python -m pytest`、现有质量检查、`python -m project_knowledge --version`、`npm pack --dry-run` 和发布知识 `finalize` 流程。

## 11. 预计改动与工作量

预计新增或修改 12～18 个文件，主要位于 `npm/`、`scripts/`、`src/project_knowledge/service.py`、`src/project_knowledge/util.py`、测试、README、审计和 CI。预计实现及测试代码 800～1400 行。

| 工作项 | 估算 |
| --- | ---: |
| 需求测试、npm 模板和构建 staging | 0.5～1 人日 |
| Python 发现、venv、wheel、锁和恢复 | 1～1.5 人日 |
| CLI/MCP 透明转发与 CodeGraph 定位 | 0.5～1 人日 |
| Codex TOML、AGENTS、幂等和卸载 | 0.75～1 人日 |
| Windows npm tarball 端到端与负向测试 | 0.75～1 人日 |
| 文档、版本、审计、CI 和知识同步 | 0.5 人日 |
| 合计 | 4～6 人日 |

加入 CodeGraph npm 依赖和发布产物验证后，完整估算从初步的 3～5 人日修正为 4～6 人日。若实现中发现必须自动下载 Python、通用重写任意 TOML、修改 CodeGraph 发布物、或工作量预计超过 6 人日，立即停止并由用户决定缩减或放弃，不扩展范围。

## 12. 完成定义

- 用户在已安装 Node、npm、Python 3.11+ 的 Windows 机器上只执行 npm 全局安装和项目根目录 `project-kb init`；
- 不要求用户手工创建 venv、运行 pip、定位 Python 或编辑 Codex 配置；
- 真实 npm tarball 的全局安装、初始化和 MCP 工具调用通过；
- 原 Python CLI、MCP 工具契约和检索评测不回归；
- 正负测试、Windows 原生 E2E、文档、版本、CHANGELOG、审计和知识同步完成；
- npm 发布元数据、wheel、Plugin 与 CLI 版本全部对应唯一版本源；
- 审计只在证据齐全后标记 NPM-001～007 完成，跨平台能力仍保持未验证。
