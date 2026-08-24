# Project Knowledge CLI 兼容性矩阵

> 当前版本：0.1.48  
> 配置模式版本：1  
> 默认语言：中文  
> 更新日期：2026-08-24

本文档描述当前已验证的兼容范围、安装边界和仍需真实环境复验的事项。版本号以 `src/project_knowledge/__init__.py` 为单一构建来源；执行补丁升级时，Codex 插件清单会同步更新。

## 1. 运行时与操作系统

| 环境 | 支持状态 | 已验证内容 | 限制与复验要求 |
| --- | --- | --- | --- |
| Python 3.11 | 支持 | `pyproject.toml` 声明最低版本 | 发布前仍需在独立 3.11 CI 执行完整测试 |
| Python 3.12 + WSL2 | 已验证 | 完整单元/集成测试、相对路径、只读真实项目、构建检查 | 当前主要开发与验收环境 |
| Linux 原生 | 设计支持 | pathlib、Git CLI、SQLite 均使用跨平台接口 | 发布前需增加原生 Linux CI |
| Windows 10/11 x64 | 已验证 npm 安装路径 | Node 20/npm 10、Python 3.11+ 探测、全局 tarball 安装、托管 venv、CodeGraph 初始化、Codex TOML、MCP stdio 与幂等卸载 | Git hooks 与更广泛 Windows 版本仍需持续 CI 复验 |
| macOS | 未验证 | 无已知平台专用依赖 | 发布前需增加 macOS CI |

知识证据只接受项目内相对路径。WSL/Linux 相对路径可以正常读取；`C:\\...` 形式的 Windows 绝对路径不会被当作项目内路径。

## 2. 配置兼容性

| 项目 | 当前行为 |
| --- | --- |
| 配置文件 | `.project-kb.yml`；同时兼容 JSON 内容和当前受限 YAML 解析 |
| 当前模式 | `config-v1.json`，初始化时发布到 `.project-kb/schemas/config-v1.json` |
| 旧版本迁移 | `project-kb migrate --dry-run` 预览，`project-kb migrate` 将版本 0 升为版本 1 |
| 用户扩展字段 | JSON 迁移完整保留未知字段；Schema 允许扩展字段 |
| YAML 注释 | 迁移只替换 `version` 行，其余文本和注释保持不变 |
| 更高版本配置 | 明确拒绝版本大于 1 的配置，避免旧程序静默误读新语义 |
| 回滚 | 迁移前应由调用方使用 Git 或备份保留旧配置；当前命令不自动生成备份 |

“前向兼容”在 0.1.10 的含义是：未知字段不丢失、未知更高模式显式失败。它不表示旧二进制可以解释未来新增的必选语义。

## 3. Model Provider

| Provider ID | 网络 | 用途 | 启用条件 | 状态 |
| --- | --- | --- | --- | --- |
| `disabled` | 否 | 完全静态、本地优先模式 | 默认值 | 支持 |
| `fake` | 否 | 测试结构化生成、缓存、断点和提案工作流 | `enabled: true` | 支持 |
| `http-json` | 是 | 调用兼容 JSON 请求/响应的外部模型服务 | `enabled: true`、`allow_network: true`、endpoint；密钥通过环境变量 | 支持基础协议，具体厂商需适配验收 |
| 其他 ID | 未定义 | — | — | 拒绝并报告能力警告 |

所有 Provider 输出在进入知识提案前都经过结构校验和敏感信息处理。Provider 生成内容不能直接覆盖人工维护区，只能进入提案/审核流程。

## 4. 客户端与插件适配

| 客户端 | 安装目标 | 安装参数 | 所有权与卸载行为 | 状态 |
| --- | --- | --- | --- | --- |
| Codex | `AGENTS.md`、`.codex/config.toml`、`.project-kb/mcp.json` | `project-kb init` 自动安装 | 只维护 `project-kb:instructions` 和 `project-kb:codex-mcp` 区块；冲突的用户自有 MCP 表会明确失败 | Windows npm 路径已验证 |
| Claude Code | `.claude/CLAUDE.md` | `--client claude` | 只维护 `project-kb:claude` 区块 | 基础支持 |
| Cursor | `.cursor/rules/project-knowledge.mdc` | `--client cursor` | 只维护 `project-kb:cursor` 区块 | 基础支持 |
| Gemini CLI | `GEMINI.md` | `--client gemini` | 只维护 `project-kb:gemini` 区块 | 基础支持 |

不传 `--client` 时会处理 Claude、Cursor 和 Gemini 三种适配。重复安装是幂等的，不会重复所有权区块；卸载仅移除工具拥有的区块和集成文件，不删除 `.project-kb/index.db`、知识文档或用户自定义内容。

## 5. npm 安装边界

| 项目 | 当前约束 |
| --- | --- |
| npm 包 | `project-kb-cli`；Node 只负责运行时引导和参数转发，产品行为仍由 Python 分发 `project-knowledge-cli` 的同版本 wheel 提供 |
| Node/npm | Node.js 20+、npm 10+ |
| Python | 3.11+；依次检查 `PROJECT_KB_PYTHON`、Windows `py -3.11`、`python`、`python3` |
| CodeGraph | npm 依赖固定为 `@colbymchenry/codegraph@1.5.0`，启动器通过绝对 `CODEGRAPH_COMMAND` 传给 Python |
| 托管运行时 | `%LOCALAPPDATA%\ProjectKnowledgeCLI\runtimes\<版本>`；无 `LOCALAPPDATA` 时回退 `%USERPROFILE%\.project-kb\runtimes\<版本>`；完成标记验证包版本、wheel SHA-256 和完成时间，死亡 PID 陈旧锁可恢复 |
| 更新 | 新 npm 版本创建新的版本目录；项目再次执行 `project-kb init` 后更新 `.codex/config.toml` 的绝对 Python 路径 |
| 覆盖 | `PROJECT_KB_PYTHON` 指定解释器，`PROJECT_KB_RUNTIME_HOME` 指定运行时根目录，已有 `CODEGRAPH_COMMAND` 保持优先 |

安装后验证由 `scripts/validate_npm_bootstrap.py` 完成：构建 wheel 和 tarball、安装到隔离 npm prefix、初始化临时 Git 项目、重复初始化、执行 MCP `initialize/tools/list/knowledge_status`，最后确认卸载只删除自有标记。

## 6. 版本与发布约束

- 当前核心版本和 Codex 插件版本均为 `0.1.48`。
- 每一批功能修改只执行一次 `python scripts/bump_version.py "中文变更说明"`，递增补丁版本。
- 构建版本从核心包的 `__version__` 动态读取；升级器同步更新 `CHANGELOG.md` 和插件 `plugin.json`。
- 发布前必须执行完整测试、构建 wheel/sdist、检查制品文件名和制品内版本，并在目标 Python/操作系统矩阵复验。
- 0.1.10 尚未承诺稳定的 Python API、配置 v2 自动迁移或任意第三方 Provider 协议兼容。

## 7. 0.1.10 构建验证记录

- wheel：`project_knowledge_cli-0.1.10-py3-none-any.whl`，元数据确认 `Version: 0.1.10`、`Requires-Python: >=3.11`。
- sdist：`project_knowledge_cli-0.1.10.tar.gz`，已确认包含 `config.py`、`schemas.py`、`service.py` 和 `versioning.py`。
- 当前 Linux Python 缺少 `build` 与 `pip` 模块，因此使用 Codex 附带 Python 的 pip/setuptools 后端在系统临时目录完成离线构建；制品未写入仓库。
- setuptools 报告旧式 `project.license` 表和 License classifier 弃用警告。该警告不阻断 0.1.10 构建，但正式发布流水线必须迁移到 SPDX license 表达式并在目标构建环境复验。
- 0.1.13 同样已构建 wheel/sdist；当前版本复验仍收到同一 setuptools 弃用警告，未写入仓库制品目录。


## 8. 历史 0.1.14 构建验证记录

- wheel：.tmp-dist-0.1.14/project_knowledge_cli-0.1.14-py3-none-any.whl（86.9 KiB），元数据确认 Version: 0.1.14、Requires-Python: >=3.11。
- sdist：.tmp-dist-0.1.14/project_knowledge_cli-0.1.14.tar.gz（98.5 KiB），已由 setuptools build_sdist 成功生成。
- 构建仍报告 setuptools 旧式 license 表与 License classifier 弃用警告；不阻断构建，正式发布前迁移到 SPDX。
