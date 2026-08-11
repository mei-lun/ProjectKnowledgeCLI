# CodeGraph 驱动的两层开发指导设计

日期：2026-08-11
状态：已获用户确认，进入实现准备
目标项目：`D:\\Github-Poj\\gardenserver`
关联阶段：WP-CG-01 / WP-GUIDE-01
默认语言：中文

## 1. 目标

第一阶段把已安装的 CodeGraph 作为可信代码事实引擎，构建可迁移、可适配、可持续更新的开发指导。最终指导不是登录、花园、公会三个具体功能的说明，而是三类开发方法：

1. 普通活动开发；
2. 普通玩家功能开发；
3. 登录模块开发。

登录、花园、公会代码只作为提炼和验收这些方法的真实证据样本。

## 2. 知识分层

### 2.1 第一层：可迁移方法论

描述一类功能在不同项目中都可复用的分析和开发方法，例如需求拆分、状态管理、数据流、扩展点、测试和发布检查。它不绑定 gardenserver，不因普通业务代码变化自动重写。

第一阶段由大模型基于通用工程知识和真实样本提炼；所有项目事实必须与第一层分离。

### 2.2 第二层：项目适配

描述在 gardenserver 中如何落地第一层方法，包括目录、框架约束、注册方式、协议、配置、跨节点调用、测试位置和常见遗漏。每个适配结论必须有 CodeGraph 或源码证据。

代码变化只自动更新第二层及其证据；第一层变化必须通过显式方法论更新流程产生建议并审核。

## 3. 生成物单目录约束

gardenserver 中所有由 ProjectKnowledgeCLI 生成、维护或缓存的文件统一放在：

```text
gardenserver/.project-kb/
```

第一阶段禁止创建或写入 `docs/knowledge/`、独立 `knowledge/`、独立 `.codegraph/` 等其他生成目录。`.project-kb/` 内部统一存放：

```text
.project-kb/
├── manifest.json       # 项目、CodeGraph、指导版本和依赖清单
├── state/              # 同步状态、锁和失败信息
├── codegraph/          # CodeGraph 运行元数据及导出结果
├── evidence/           # 登录、花园、公会等样本证据包
├── methodology/        # 第一层方法论结构化源文件
├── guides/             # 第二层项目适配结构化源文件
├── generated/          # 面向用户的中文 Markdown
├── proposals/          # 方法论或适配变更建议
└── logs/               # 同步和生成日志
```

目录内部可以分层，但仓库外不再散落本项目生成文件。原项目已有源码、文档、配置和用户修改不覆盖、不移动。

## 4. 组件边界

### 4.1 CodeGraph Adapter

适配器只使用 CodeGraph 的公开 CLI/API，不读取私有数据库。它负责：

- `init`、`sync`、`status` 和 JSON 查询；
- 符号、源码、调用者、被调用者、影响范围和测试查询；
- Windows 安装路径发现以及 WSL/Windows 路径转换；
- 版本和能力检测、进程超时、JSON 解析和错误报告。

### 4.2 gardenserver 规则适配器

基于 CodeGraph 结果和源码规则识别 Lua/Skynet/zn 结构：模块、服务、`zn.func_mod`、Avatar 组件/系统注册、协议、配置、跨节点 RPC、测试和约束。规则不能把不存在的统一框架当作事实；证据不足时输出待确认项。

### 4.3 Evidence Pack

证据包记录查询、结果、源码路径、符号/行号、时间、CodeGraph 版本、结果哈希和新鲜度。大模型只接收受限证据包和第一层方法论，不直接扫描未授权文件。

### 4.4 结构化生成与校验

大模型生成结构化方法论或项目适配草案；校验器检查文件、符号、行号、哈希和层级边界。未经校验的内容不得标记为 `verified`。

### 4.5 Markdown 渲染器

从结构化源文件生成中文文档。每份文档必须区分：`可迁移方法论`、`gardenserver 项目适配`、`CodeGraph 事实证据` 和 `待人工确认`。

## 5. 三类指导的第一阶段内容

### 5.1 普通活动开发

覆盖需求拆分、生命周期、开启/结束、玩家状态、配置、奖励、幂等、定时器、协议、持久化、测试、灰度和回滚；项目层从 gardenserver 现有活动、定时器、奖励和状态代码中提炼，缺失机制明确标记。

### 5.2 普通玩家功能开发

覆盖领域对象、数据组件、业务系统、消息入口、协议、配置、事件、持久化、异常、测试和扩展；项目层重点映射 gardenserver 的 `msg`、`system`、`com`、Avatar 注册、配表和框架约束。

### 5.3 登录模块开发

覆盖入口、校验、认证、账号/角色查询、会话、节点分配、跨节点通信、返回协议、异常、安全、测试和部署；项目层映射 login 节点、DAO、session、cluster RPC 和相关配置。

## 6. 同步流程

提供：

```bash
project-kb sync D:\\Github-Poj\\gardenserver
project-kb watch D:\\Github-Poj\\gardenserver
```

同步流程：CodeGraph 增量同步 → 计算变化 → 映射受影响指导类别 → 重新采集证据 → 更新第二层项目适配 → 校验引用 → 渲染中文 Markdown → 写入单目录状态。

CodeGraph 或模型不可用时，不覆盖上一份有效指导；将状态标记为过期/待处理，并保存错误日志。

## 7. 版本与审核

每批实现运行一次 `python scripts/bump_version.py "中文变更说明"`，同步知识不重复递增。验证 `python -m project_knowledge --version` 和 `CHANGELOG.md`。方法论变更生成 proposal，不能由普通代码同步静默覆盖。

## 8. 第一阶段验收

- gardenserver 的 PKS 产物在 `.project-kb/` 内完成初始化和同步，CodeGraph 运行时索引保留在其要求的 `.codegraph/`；
- 适配器能查询符号、源码、调用关系、影响范围和测试；
- 生成三类中文指导，且第一层、第二层、证据明确分离；
- 登录、花园、公会样本可回溯到源码证据；
- 修改样本代码后，CodeGraph 和受影响的第二层指导能够更新；
- 失效引用、CodeGraph 失败或模型失败时不会产生未标记的虚假事实；
- gardenserver 外部不产生其他 ProjectKnowledgeCLI 生成目录。

## 9. 非目标

本阶段不重写 CodeGraph、不读取私有数据库、不自动修改业务代码、不把具体功能生成物作为长期知识主体、不保证自动把项目特例提升为通用方法论，也不实现多项目共享治理。


## 10. CodeGraph 运行时目录限制

PKS 自有文件统一写入 `.project-kb/`。CodeGraph 1.5 的 Windows 运行时固定通过项目根目录下的 `.codegraph/` 发现索引；在 WSL 调用 Windows Node 时，环境变量不能可靠改变这一发现路径，且把索引搬到 `.project-kb` 后上游会报告“未初始化”。因此 `.codegraph/` 保留为外部引擎运行时目录，并在 `.project-kb.yml` 中显式配置/排除；PKS 不读取其私有数据库，只通过公开 CLI 获取事实。
