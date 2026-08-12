# README 目录用途说明 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 README 表格解释 `.project-kb` 全部目录用途，并固定人工维护规则。

**Architecture:** README 是目录说明唯一入口；不新增代码目录注册表。版本和下一版本文档只做必要的一致性调整。

**Tech Stack:** Markdown、Python unittest、版本脚本。

---

### Task 1: README 目录说明

**Files:**
- Modify: `README.md`

- [ ] 增加命名由来、根文件、子目录、外部 `.codegraph` 四张表。
- [ ] 增加“目录变更必须同批更新 README”的人工维护约定。
- [ ] 明确 `.project-kb/codegraph` 是预留目录，`.codegraph` 是 CodeGraph 1.5 实际目录。

### Task 2: 版本计划顺延

**Files:**
- Modify: `docs/next-version-plan.md`
- Modify: `tests/test_documentation_roadmap.py`

- [ ] 将下一功能版本从 `0.1.18` 顺延为 `0.1.19`。
- [ ] 更新既有文档一致性测试；不增加目录自动覆盖测试。

### Task 3: 发布和验证

**Files:**
- Modify: `src/project_knowledge/__init__.py`
- Modify: `plugins/project-knowledge/.codex-plugin/plugin.json`
- Modify: `CHANGELOG.md`

- [ ] 运行版本脚本递增到 `0.1.18`。
- [ ] 同步本地知识库。
- [ ] 运行文档、版本和全量测试。
- [ ] 检查 `LICENSE` 保持未提交且未被修改。
