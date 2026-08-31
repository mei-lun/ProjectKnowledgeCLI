# Windows Codex + Pi 一键安装设计

日期：2026-08-31  
工作包：WP-INSTALL-WIN-01  
范围：Windows 10/11、Codex CLI、Pi coding agent

## 1. 背景与目标

当前 project-kb 的项目初始化会把当前机器的 `sys.executable` 和 CodeGraph 绝对路径写入项目内 `.codex/config.toml`。这会导致配置被复制到另一台电脑后仍指向旧 Python 环境；当 Codex 使用不同的 `CODEX_HOME` 时，用户级配置也不会被发现。Pi 没有内置 MCP 客户端，当前项目没有原生 Pi 扩展，因此即使 npm CLI 已安装，Pi 也看不到 project-kb 工具。

本工作包参考 codegraph 的“CLI 安装”和“代理接入”分离模型，提供 Windows 用户级安装器：

```powershell
npm install -g project-kb-cli
project-kb install --target codex,pi --location global --yes
```

安装后，Codex 和 Pi 均能在新机器上通过稳定的 `project-kb` 入口启动，不依赖旧绝对路径；重复安装和升级只刷新 project-kb 自有内容。

## 2. 范围与非目标

### 2.1 本期范围

- 仅支持 Windows 10/11。
- 支持 Codex CLI 全局安装和 Pi 全局原生扩展安装。
- 尊重 `CODEX_HOME`；未设置时使用 `%USERPROFILE%\\.codex`。
- 尊重 `PI_CODING_AGENT_DIR`；未设置时使用 `%USERPROFILE%\\.pi\\agent`。
- Codex 使用稳定的 `project-kb` 命令启动 stdio MCP，不把 Python 或 CodeGraph 绝对路径写入新配置。
- Pi 扩展通过当前工作目录调用 `project-kb mcp --project <cwd>`，注册 project-kb 只读工具。
- 安装、刷新、卸载均使用 marker 所有权边界，保留用户配置和 `.project-kb` 数据。
- 保留现有项目级 `project-kb init` 行为；项目级配置同样迁移到稳定启动入口。

### 2.2 非目标

- 不在本工作包承诺 Linux、macOS 或 WSL。
- 不自动安装 Node.js、Python、Codex 或 Pi 本体。
- 不改写知识检索、CodeGraph 适配器或 MCP 工具业务语义。
- 不在 npm 卸载时扫描和删除项目知识库。
- 不覆盖用户自有的同名 Codex MCP 配置。

## 3. 需求与验收

| ID | 要求 | 验收证据 |
| --- | --- | --- |
| WIN-001 | 全局 npm 安装后可执行 `project-kb` | 隔离 npm prefix 从真实 tarball 安装并通过 `project-kb --version` |
| WIN-002 | 安装器发现并复用托管 Python 运行时 | 首次、重复、损坏 marker、旧版本和低版本 Python 测试 |
| WIN-003 | Codex 全局配置使用正确的 `CODEX_HOME` | 设置/切换 `CODEX_HOME` 的正负测试；配置 TOML 可解析 |
| WIN-004 | Codex 配置不绑定机器绝对 Python/CodeGraph 路径 | 生成配置只含稳定 `project-kb` 启动命令；旧 marker 区块可刷新 |
| WIN-005 | Pi 全局扩展可加载并注册工具 | 扩展文件生成、幂等、工具执行和路径覆盖测试 |
| WIN-006 | 用户配置和知识数据安全保留 | 同名非托管 MCP 冲突拒绝；卸载保留用户文本和 `.project-kb` |
| WIN-007 | 真实安装后 Codex MCP 可用 | `initialize`、`tools/list`、`knowledge_status` 通过 |
| WIN-008 | 安装自检可定位环境问题 | `project-kb doctor` 报告 CODEX_HOME、Pi、PATH、运行时和 CodeGraph 状态 |

## 4. 架构

### 4.1 安装入口

Node 启动器继续负责托管 Python 环境和 CodeGraph 依赖解析，并新增 Windows agent installer 模块。`project-kb install` 在 npm 包环境下优先执行 Node 安装器；Python CLI 的项目级集成逻辑保留为内部 API，避免重复实现知识库业务。

安装器解析：

1. `CODEX_HOME`，否则 `%USERPROFILE%\\.codex`；
2. `PI_CODING_AGENT_DIR`，否则 `%USERPROFILE%\\.pi\\agent`；
3. npm 全局 bin 中当前 `project-kb` 启动器路径，用于诊断和可选绝对回退；
4. 当前 npm 包版本对应的托管 Python 和 CodeGraph 路径。

### 4.2 Codex 适配

全局文件：

- `%CODEX_HOME%\\config.toml`
- `%CODEX_HOME%\\AGENTS.md`

所有权区块：

```toml
# project-kb:codex-mcp:start
[mcp_servers.project_knowledge]
command = "project-kb"
args = ["mcp", "--project", "."]
# project-kb:codex-mcp:end
```

配置不写 `cwd`、`sys.executable` 或 `CODEGRAPH_COMMAND`。Node 启动器启动时根据自身包位置创建/复用 Python 环境，并向 Python 子进程注入 CodeGraph 绝对路径。这样 Codex 配置可以跨机器复制；新机器只需重新安装 npm 包并刷新自有区块。

对旧版本 marker 区块，安装器替换为上述稳定区块；对无 marker 但已有 `mcp_servers.project_knowledge` 的配置，返回冲突并保持原文件不变。TOML 更新使用窄范围表写入并在写后解析验证。

### 4.3 Pi 适配

全局文件：

```text
%PI_CODING_AGENT_DIR%\\extensions\\project-kb.ts
```

扩展导出默认函数，使用 Pi 的 `registerTool` 注册：

- `knowledge_status`
- `knowledge_context`
- `knowledge_search`
- `knowledge_get`
- `knowledge_impact`

每个工具在执行时使用 Pi 当前会话工作目录，调用稳定的 `project-kb` CLI，通过一次 MCP JSON-RPC 请求完成工具调用并返回文本/结构化结果。扩展不保存 Python 路径，不依赖项目内 `.codex` 文件。若全局和项目扩展同时存在，扩展在注册前检查 `getAllTools()`，避免重复工具冲突。

扩展文件带有 `project-kb:pi-extension:start/end` marker。安装只替换 marker 所有内容；卸载删除自有文件或自有区块，不删除用户创建的同名扩展。

### 4.4 项目级兼容

现有 `project-kb init` 继续生成项目级 `.codex/config.toml`，但配置改为稳定 `project-kb` 命令，并移除旧的绝对 Python/CodeGraph 环境字段。项目级安装仍要求 Codex 信任该项目；全局安装不依赖项目是否已初始化，MCP 工具在未初始化项目中返回现有明确错误。

## 5. 数据流与错误处理

```text
npm global install
  -> project-kb install
  -> resolve CODEX_HOME / PI_CODING_AGENT_DIR
  -> atomic upsert Codex TOML + AGENTS.md
  -> atomic write Pi extension
  -> report changed paths and restart requirement

Codex/Pi tool call
  -> project-kb launcher
  -> ensureRuntime(version, wheel hash)
  -> managed Python -m project_knowledge mcp --project <cwd>
  -> CodeGraph command injected by launcher
```

错误要求：

- `CODEX_HOME` 或 Pi 目录不可写：报告单一路径和权限修复建议，不修改其他位置。
- TOML 无法解析：拒绝写入并保留原文件。
- 用户自有同名 MCP/扩展：返回稳定冲突错误，不覆盖。
- `project-kb` 不在 PATH：安装器报告 npm global bin 和重启 shell 提示；Codex 配置仍保持稳定命令。
- 托管运行时损坏：删除本次临时目录，保留既有完整运行时，下一次调用可重建。
- MCP 子进程失败：原样保留退出码和 stderr，Pi 扩展返回可读错误文本。

## 6. 测试与验证

### 6.1 Node 单元测试

- `CODEX_HOME` 和 `PI_CODING_AGENT_DIR` 的解析、覆盖和 Windows 路径空格。
- Codex TOML marker upsert、旧绝对路径迁移、非托管冲突和幂等。
- Pi 扩展内容生成、重复安装、卸载所有权边界。
- CLI 入口转发、MCP 请求转发、退出码和信号处理。

### 6.2 Python 回归测试

- 现有项目级 `init/install/uninstall` 测试更新为稳定 `project-kb` 配置。
- 旧 `.codex/config.toml` 迁移后 TOML 可解析且用户内容保持。
- CodeGraph 初始化失败时不留下新的 agent 集成。

### 6.3 Windows 真实验证

1. 构建 wheel 和 npm staging，执行 `npm pack`。
2. 在隔离 npm prefix 安装 tarball。
3. 设置临时 `CODEX_HOME` 和 `PI_CODING_AGENT_DIR`，执行 `project-kb install --target codex,pi --location global --yes`。
4. 解析 Codex TOML，启动生成的 MCP 命令，执行 `initialize/tools/list/knowledge_status`。
5. 重复安装并确认 agent 文件字节稳定；切换 `CODEX_HOME` 后确认写入新目录。
6. 初始化临时 Git 项目，确认项目级配置使用稳定命令。
7. 执行卸载，确认用户区块和 `.project-kb` 数据保留。
8. 运行 `project-kb doctor` 和仓库完整测试。

## 7. 版本、文档与知识同步

- 实现批次完成前运行 `python scripts/bump_version.py "Windows Codex 与 Pi 一键安装"`，只递增一次补丁版本。
- `python -m project_knowledge --version`、npm staging、Plugin 元数据和 CHANGELOG 必须使用唯一版本源。
- 更新 README、兼容性矩阵和审计报告，只有 WIN-001～WIN-008 的证据齐全后才标记完成。
- 源码变更后运行 `project-kb sync --task-summary`；若 curated 知识变为 `potentially_stale`，在交付报告中列出人工复核项。

## 8. 完成定义

Windows 新电脑只需安装 Node/npm、Python 3.11+、Codex/Pi 本体和 project-kb npm 包，然后执行一次全局 agent 安装命令；不需要手工编辑 `config.toml`、设置旧 Python 路径或重新猜测 `CODEX_HOME`。Codex MCP 工具和 Pi 原生工具均能从当前项目目录启动，重复安装、切换用户目录、升级和卸载行为都有自动化证据。
