# Project Knowledge CLI

Project Knowledge System（PKS）是面向代码仓库的本地优先项目知识库。它将源码结构索引到可重建的 SQLite 数据库，生成可追溯到来源的 Markdown 知识，检测可能过期的人工维护知识，并通过只读 MCP 服务器提供精简的任务上下文。

MVP 基于 Python 3.11+，不依赖第三方运行时包。默认配置完全本地运行且禁止网络。当前产品范围只包含本地建库和代码变化后的自动同步；模型生成、外部 CodeGraph、多客户端和团队治理不是当前验收目标。

## 当前核心能力

- `project-kb init`：在本地建立 SQLite 索引和默认中文知识文档；
- `project-kb watch`：持续检测源码修改、新增和删除，并刷新索引与自动生成知识；
- `project-kb status` / `check`：检查待同步文件、内容新鲜度和 watcher 健康；
- MCP 五个只读工具和 `docs/knowledge/generated/`：查询或直接阅读知识。

## 快速开始

```bash
python -m pip install -e .
project-kb init /path/to/repository
project-kb status /path/to/repository
project-kb watch /path/to/repository
# 另一个终端中按需运行：
project-kb mcp --project /path/to/repository
```

初始化会创建 `.project-kb.yml`、本地 `.project-kb/index.db`、版本化清单和 `docs/knowledge`。自动生成的知识可由 PKS 覆盖；人工维护文档的初始模板创建后，PKS 不会静默修改其正文。

## 版本管理

项目以 `0.1.0` 为版本基线，采用 `major.minor.patch` 格式。唯一版本源是 `src/project_knowledge/__init__.py`，构建元数据、`project-kb --version` 和 MCP 服务均读取该值。

从下一批修改开始，任何修改或新增内容都需要在同一批变更中递增一次补丁版本号：

```bash
python scripts/bump_version.py "本次变更的中文说明"
```

例如 `0.1.0` 的下一版本是 `0.1.1`。命令会同时更新唯一版本源和 `CHANGELOG.md`；使用 `--dry-run` 可以只预览结果。自动生成知识的重复同步不需要再次递增版本号。

## 命令

```text
project-kb init [path]       初始化并索引仓库
project-kb sync [path]       立即同步已变更文件
project-kb rebuild [path]    原子重建索引
project-kb watch [path]      持续检测变更并自动同步
project-kb status [path]     显示索引和知识库健康状态
project-kb check [path]      执行健康检查
project-kb doctor [path]     检查本地运行环境和项目配置
project-kb mcp              运行只读 stdio MCP 服务器
```

所有写入命令均支持 `--dry-run`、`--json` 和 `--quiet`。使用 `project-kb <command> --help` 查看选项。其他历史扩展命令保留兼容，但不属于当前最小产品范围。

## 知识来源标记

人工维护的 Markdown 可以声明来源依赖，而无需将正文所有权交给 PKS：

```markdown
<!-- project-kb:source file="src/auth/service.py" -->
<!-- project-kb:source symbol="src/auth/service.py::AuthService.authenticate" -->
```

PKS 会在 `.project-kb/manifest.json` 中记录当前来源哈希。来源发生变更时状态变为 `potentially_stale`，来源被删除时状态变为 `stale`。

## MCP 工具

服务器提供 `knowledge_context`、`knowledge_search`、`knowledge_get`、`knowledge_impact` 和 `knowledge_status`。工具结果包含可信度、新鲜度和来源引用。服务器通过 stdio 支持逐行 JSON-RPC，同时兼容当前无状态请求格式和旧版 `initialize` 客户端。

架构、系统保证和路线图请参阅[系统设计文档](docs/project-knowledge-system-design.md)。

当前实现与原始需求的逐项完成度、关键缺口、工作包依赖和后续验收标准，请参阅[需求对齐审计与后续实施基线](docs/project-knowledge-system-audit.md)。后续功能开发以该审计报告为执行清单。
