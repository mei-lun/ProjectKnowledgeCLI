# README 目录用途说明设计（方案 A）

日期：2026-08-12

## 目标

在 README 中用表格说明 `project-kb` 命名由来、`.project-kb/` 根文件、所有当前子目录及外部 `.codegraph/` 的用途，并增加人工维护约定。

## 范围

- 只维护 README，不增加代码级目录清单或自动目录覆盖测试。
- README 明确区分 PKS 自有目录、运行状态文件、用户维护知识和 CodeGraph 外部目录。
- 新增、删除、改名或改变职责时，开发者必须在同一批变更中人工更新 README。
- 本批文档版本为 `0.1.18`，原 `0.1.18` 功能计划顺延到 `0.1.19`。

## 目录覆盖

| 类别 | 路径 |
| --- | --- |
| 根文件 | `.project-kb.yml`、`index.db`、`manifest.json`、`index.md`、`mcp.json`、`state.json` |
| 自动过程 | `events/`、`logs/`、`state/`、`schemas/` |
| 语义治理 | `drafts/`、`curated/`、`decisions/`、`proposals/`、`proposals/queue/` |
| 两层指导 | `evidence/`、`methodology/`、`guides/`、`generated/` |
| 引擎兼容 | `.project-kb/codegraph/`（预留）、项目根 `.codegraph/`（CodeGraph 1.5 实际运行时） |

## 验收

- README 包含 Project Knowledge Base 缩写解释。
- 所有上述路径均有中文用途、所有权和维护建议。
- README 明确方案 A 依赖人工同步，不宣称自动保障。
- README、下一版本计划、文档契约测试、版本和 CHANGELOG 一致为 `0.1.18/0.1.19`。
