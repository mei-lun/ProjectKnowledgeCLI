# Codex 项目级 MCP 迁移

自 `0.1.59` 起，Project Knowledge 的 Codex MCP 只写入当前项目的 `.codex/config.toml`，服务器名固定为 `project_knowledge`，并通过绝对 `cwd` 绑定项目根目录。用户级 Codex 配置不再由普通 `init`、`install` 或 npm 启动器写入。

旧用户在项目目录执行：

```powershell
project-kb migrate --codex-scope project
project-kb doctor
project-kb status
```

迁移会先创建带时间戳的用户级 `config.toml.project-kb-backup-*` 备份，只删除能确认属于当前项目的 `project_knowledge` 或 `project_knowledge_*` 条目，并保留其他 MCP 配置。完成后需要重新加载或重启 Codex MCP servers。

全局 agent 安装仅保留给非 Codex 客户端：

```powershell
project-kb agent install --global --target pi
project-kb agent uninstall --global --target pi
```

对 Codex 使用全局安装会失败，并提示在项目内运行 `project-kb init` 或 `project-kb install --client codex`。
