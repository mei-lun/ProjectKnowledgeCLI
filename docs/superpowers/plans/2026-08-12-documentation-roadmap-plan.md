# 当前状态与路线图文档分层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用三个职责清晰的中文文档分别呈现当前能力、下一版本执行范围和长期候选特性。

**Architecture:** README 是发布现状入口；`next-version-plan.md` 是唯一近期实施清单；`future-features.md` 是非承诺候选池。通过文档回归测试约束版本、链接、关键状态和过期表述。

**Tech Stack:** Markdown、Python `unittest`、Project Knowledge CLI 版本脚本。

---

### Task 1: 建立文档契约测试

**Files:**
- Create: `tests/test_documentation_roadmap.py`

- [ ] 编写失败测试，要求 README、下一版本计划和未来特性文件存在并互相链接。
- [ ] 断言 README 包含 `0.1.17`、CodeGraph 已接入、`.project-kb/generated`，且不包含“外部 CodeGraph 不是当前范围”。
- [ ] 运行 `PYTHONPATH=src python3 -m unittest tests.test_documentation_roadmap -v`，确认在文档创建前失败。

### Task 2: 重构当前状态 README

**Files:**
- Modify: `README.md`

- [ ] 用表格呈现产品定位、当前能力、CLI、MCP、gardenserver 验证和已知限制。
- [ ] 保留最小快速开始和版本管理命令。
- [ ] 添加下一版本计划、未来特性、审计和设计文档导航。

### Task 3: 新增下一版本计划

**Files:**
- Create: `docs/next-version-plan.md`

- [ ] 将 `0.1.18` 范围固定为指导注册到 KnowledgeStore、增量端到端验收、项目适配器注册和结构化指导增强。
- [ ] 为每项写需求 ID、正负验收、工作量、依赖和完成定义。
- [ ] 明确共享 daemon、向量检索和生产模型不进入该版本。

### Task 4: 新增未来特性清单

**Files:**
- Create: `docs/future-features.md`

- [ ] 分近期候选、中期增强、长期方向和明确非目标。
- [ ] 为每项记录价值、前置条件、优先级、风险和迁入下一版本的条件。

### Task 5: 版本、知识同步和验证

**Files:**
- Modify: `src/project_knowledge/__init__.py`
- Modify: `plugins/project-knowledge/.codex-plugin/plugin.json`
- Modify: `CHANGELOG.md`

- [ ] 运行 `python3 scripts/bump_version.py "重构 README 并建立下一版本和未来特性路线图"`，版本递增到 `0.1.17`。
- [ ] 运行文档测试、版本测试和全量单元测试。
- [ ] 运行 `PYTHONPATH=src python3 -m project_knowledge sync . --json` 同步生成知识。
- [ ] 检查差异，确认不提交用户原有的 `LICENSE` 修改。
