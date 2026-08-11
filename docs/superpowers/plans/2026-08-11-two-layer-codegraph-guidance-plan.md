# 两层 CodeGraph 开发指导实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 接入已安装的 CodeGraph，在 `D:\\Github-Poj\\gardenserver` 的单一 `.project-kb/` 目录内生成并持续更新“普通活动开发、普通玩家功能开发、登录模块开发”三类两层中文开发指导。

**Architecture:** 保留现有 `CodeIndexEngine` 抽象，新增公开 CLI 驱动的 `CodeGraphEngine`。CodeGraph 提供代码事实，gardenserver 规则适配器提取 Lua/Skynet 结构，指导服务把可迁移方法论和项目适配分层存储，最后统一渲染到 `.project-kb/generated/`。同步时只更新受影响的第二层和证据；第一层通过显式模型提案更新。

**Tech Stack:** Python 3.12+、现有 unittest、CodeGraph 1.5 CLI、JSON/YAML 配置、SQLite 事实索引、现有 `ModelRuntime`（可选）。

---

### Task 1: 收口单目录配置与生成路径

**Files:**
- Modify: `src/project_knowledge/config.py`
- Modify: `src/project_knowledge/service.py`
- Modify: `src/project_knowledge/knowledge.py`
- Modify: `src/project_knowledge/semantic.py`
- Modify: `src/project_knowledge/proposal.py`
- Modify: `src/project_knowledge/schemas.py`
- Test: `tests/test_config.py`, `tests/test_integration.py`, `tests/test_single_directory.py`

- [ ] **Step 1: Write the failing tests**

新增测试断言新项目默认配置为 `knowledge_root=.project-kb`，生成的 manifest、索引、generated/drafts/curated/decisions、schema、events 和日志都位于 `.project-kb/`；初始化 dry-run 不再声明 `docs/knowledge/**` 或根目录 `AGENTS.md` 为知识生成物。

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m unittest tests.test_single_directory tests.test_config -v`

Expected: FAIL，当前默认路径仍为 `docs/knowledge/*`。

- [ ] **Step 3: Implement the minimal path policy**

将默认路径改为：`knowledge_root=.project-kb`、`generated_root=.project-kb/generated`、`drafts_root=.project-kb/drafts`、`curated_root=.project-kb/curated`、`decisions_root=.project-kb/decisions`；将旧 `docs/knowledge/**` 只保留为显式旧配置兼容值，不再作为新项目默认值。`KnowledgeGenerator`、`SemanticKnowledgeService` 和 `ProposalService` 所有写路径改为使用配置值，不得硬编码旧路径。初始化只创建 `.project-kb/**`；客户端 marker 仍由显式 `install` 命令管理，不由 `init` 隐式写入。

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m unittest tests.test_single_directory tests.test_config tests.test_integration -v`

Expected: PASS；旧路径相关测试改为验证显式旧配置仍可读，默认项目不产生旧目录。

### Task 2: 实现公开 CodeGraph Adapter

**Files:**
- Create: `src/project_knowledge/codegraph.py`
- Modify: `src/project_knowledge/config.py`, `src/project_knowledge/schemas.py`, `src/project_knowledge/engine.py`
- Test: `tests/test_codegraph.py`, `tests/test_engine.py`

- [ ] **Step 1: Write the failing adapter tests**

用 `unittest.mock.patch("subprocess.run")` 覆盖：Windows 安装路径解析、`CODEGRAPH_COMMAND` 覆盖、WSL 路径转换、`init/sync/status/query/source/trace/impact/affected` JSON 解析、超时、非零退出码和坏 JSON。测试 `create_engine(ProjectConfig(engine="codegraph"))` 返回适配器而不是抛出不可用错误。

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m unittest tests.test_codegraph tests.test_engine -v`

Expected: FAIL，`codegraph` 当前会抛出“不可用”。

- [ ] **Step 3: Implement the adapter**

在 `codegraph.py` 中实现 `CodeGraphCommandResolver`、`CodeGraphClient` 和 `CodeGraphEngine`。客户端使用 `subprocess.run(..., shell=False, text=True, capture_output=True)`，命令来自配置、`CODEGRAPH_COMMAND` 或 Windows 安装目录 `C:\\Users\\mei\\AppData\\Local\\codegraph\\current\\bin\\codegraph.cmd`；通过 `--help`/版本探测缓存能力。所有项目路径先转换为 CodeGraph 所在宿主机可识别的 Windows 路径。公开方法返回稳定字典/模型，保留原始 JSON 的 `raw` 字段用于证据哈希。

`CodeGraphEngine` 实现现有接口：`discover` 从 CodeGraph files 结果映射 `IndexedFile`，`parse` 读取源码位置结果，`search_symbols`、`get_source`、`trace`、`impact`、`affected_tests` 和 `entrypoints` 均委托公开 CLI，不访问数据库。命令不可用时抛出包含命令、退出码和 stderr 的 `CodeGraphError`。

- [ ] **Step 4: Run adapter tests and existing engine tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_codegraph tests.test_engine -v`

Expected: PASS；原有 builtin 测试继续通过。

### Task 3: 建立 gardenserver Lua/Skynet 规则和证据包

**Files:**
- Create: `src/project_knowledge/gardenserver.py`
- Modify: `src/project_knowledge/evidence.py`, `src/project_knowledge/models.py`, `src/project_knowledge/schemas.py`
- Test: `tests/test_gardenserver_rules.py`, `tests/test_evidence.py`

- [ ] **Step 1: Write failing rule tests**

使用最小 Lua/Skynet fixture 验证识别：`require` 模块、`zn.func_mod` 消息模块、`zn.startup_app`/`zn.startup_sf` 服务入口、Avatar components/systems 注册、`zapi.cluster`/`zn.req` RPC、`.proto`/配置读取、测试文件和禁止原生 Skynet API。测试每条规则都返回相对路径、行号、符号或原文片段。

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m unittest tests.test_gardenserver_rules tests.test_evidence -v`

Expected: FAIL，当前没有 gardenserver 专用证据采集器。

- [ ] **Step 3: Implement deterministic rules and Evidence Pack extensions**

新增 `GardenserverRuleAdapter` 和 `GuidanceEvidenceCollector`。适配器只做可解释的文本/CodeGraph 结果映射，不猜测动态运行时行为；每个 `EvidenceItem` 增加 `origin`、`query`、`result_hash`、`freshness` 和 `authority`。为三类指导定义稳定 category ID：`activity-development`、`player-feature-development`、`login-module-development`，并把登录、花园、公会作为 sample anchors 写入证据包。

- [ ] **Step 4: Run rule and evidence tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_gardenserver_rules tests.test_evidence -v`

Expected: PASS；证据包 JSON 可序列化且所有路径位于项目根目录内。

### Task 4: 实现两层结构化指导生成和中文渲染

**Files:**
- Create: `src/project_knowledge/guidance.py`
- Create: `src/project_knowledge/guidance_templates.py`
- Modify: `src/project_knowledge/semantic.py`, `src/project_knowledge/provider.py`, `src/project_knowledge/service.py`
- Test: `tests/test_guidance.py`

- [ ] **Step 1: Write failing guidance tests**

测试三类 category 都能从证据包生成结构化结果，结果包含 `methodology` 和 `project_adaptation` 两层；渲染结果包含中文标题、步骤、样本证据、源码路径/行号和“待人工确认”；代码事实缺引用或引用失效时不能标记为 `verified`。模型禁用时测试内置可迁移方法论模板加确定性项目事实渲染，模型启用时使用现有 `ModelRuntime` 做结构化提炼。

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m unittest tests.test_guidance -v`

Expected: FAIL，当前只有单层 Feature Guide 草案和 `docs/knowledge` 输出。

- [ ] **Step 3: Implement the guidance service**

`GuidanceService` 负责加载内置方法论模板、构建 category Evidence Pack、调用可选 `ModelRuntime`、执行证据校验，并生成 `GuidanceDocument`。模型输出只允许填充结构化字段，引用必须来自本次证据包；无模型时保留模板中的通用方法论并用确定性事实填充项目适配，标记 `generated` 而非 `verified`。`MarkdownGuidanceRenderer` 输出 `.project-kb/generated/开发指导索引.md` 及三份中文文档；`.project-kb/methodology/` 和 `.project-kb/guides/` 保存结构化源。

- [ ] **Step 4: Run guidance tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_guidance tests.test_semantic tests.test_provider -v`

Expected: PASS；旧 Feature Guide API 保持兼容。

### Task 5: 将指导同步接入 init/sync/watch

**Files:**
- Modify: `src/project_knowledge/service.py`, `src/project_knowledge/cli.py`, `src/project_knowledge/config.py`
- Modify: `src/project_knowledge/knowledge.py`
- Test: `tests/test_guidance_sync.py`, `tests/test_watch_wp07.py`, `tests/test_integration.py`

- [ ] **Step 1: Write failing lifecycle tests**

测试 `init` 对 gardenserver 配置触发 CodeGraph 初始化和三类指导生成；修改登录/花园/公会样本文件后运行一次 `sync`，只有受影响类别的第二层证据和 Markdown 更新；`watch --once` 复用同一流程；CodeGraph/Provider 失败时保留上一版文档、写入 `.project-kb/logs/` 并报告过期状态。

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m unittest tests.test_guidance_sync tests.test_watch_wp07 tests.test_integration -v`

Expected: FAIL，现有同步只更新 builtin SQLite 和基础 generated knowledge。

- [ ] **Step 3: Implement lifecycle integration**

在 `ProjectService.initialize` 中按配置运行 CodeGraph init、事实采集和指导初次生成；在 `sync` 完成代码索引后使用 `ChangeSet.affected_modules` 和证据依赖确定 category，刷新对应第二层并更新 manifest；watch 继续使用现有单 watcher 锁和防抖。所有输出路径通过 `ProjectConfig` 解析到 `.project-kb/`，失败采取保留旧版本策略。

- [ ] **Step 4: Run lifecycle tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_guidance_sync tests.test_watch_wp07 tests.test_integration -v`

Expected: PASS；默认 builtin 项目和 CodeGraph 项目都能完成各自生命周期。

### Task 6: gardenserver 真实项目验收

**Files:**
- Create: `scripts/validate_gardenserver_guidance.py`
- Test: `tests/test_gardenserver_real_project.py`

- [ ] **Step 1: Add an opt-in real-project test**

测试从 `GARDENSERVER_ROOT` 读取 `D:\\Github-Poj\\gardenserver` 的映射路径；未设置时跳过，设置后只读检查目标已有脏工作树，不改动源码。断言 CodeGraph 状态可读、`.project-kb/` 是唯一 ProjectKnowledgeCLI 输出目录、三类文档存在且包含登录/花园/公会证据引用。

- [ ] **Step 2: Run the real-project validation**

Run: `GARDENSERVER_ROOT=/mnt/d/Github-Poj/gardenserver PYTHONPATH=src python3 scripts/validate_gardenserver_guidance.py`

Expected: PASS；只在 `/mnt/d/Github-Poj/gardenserver/.project-kb/` 写入初始化、证据、结构化知识和中文指导，不覆盖其他用户修改。

- [ ] **Step 3: Verify update behavior with a disposable probe**

在 `.project-kb/probes/` 下创建 CodeGraph 可识别的临时 Lua probe，运行一次 `sync`，修改后再次 `sync`，最后删除 probe 并再次 `sync`；确认事实、引用和第二层指导状态随变更更新。probe 始终位于 `.project-kb/`，不污染 gardenserver 正式源码。

### Task 7: 文档、版本和知识同步

**Files:**
- Modify: `docs/project-knowledge-system-audit.md`, `docs/project-knowledge-system-design.md`, `CHANGELOG.md`
- Modify: `docs/compatibility-matrix.md`, `docs/knowledge/index.md`
- Run: `python scripts/bump_version.py "接入 CodeGraph 并实现两层中文开发指导"`

- [ ] **Step 1: Update the audit baseline**

新增当前需求修订，撤销“真实 CodeGraph Adapter 和 Feature Guide 明确不做”的旧收口描述，改为 WP-CG-01/WP-GUIDE-01 的验收条目；保留历史记录，不删除审计证据。

- [ ] **Step 2: Run the required verification**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests -q`; `PYTHONPATH=src python3 -m project_knowledge --version`; `PYTHONPATH=src python3 -m project_knowledge status . --json`; `PYTHONPATH=src python3 -m project_knowledge check . --json`.

Expected: 全部测试通过，版本为 0.1.16，CHANGELOG 有中文版本记录，curated/ADR 状态明确。

- [ ] **Step 3: Synchronize generated knowledge**

运行项目知识同步命令并检查 git diff，确保 ProjectKnowledgeCLI 自身的生成知识只按其显式配置路径更新；报告 generated 是否同步和 curated/ADR 是否需要人工复核。
