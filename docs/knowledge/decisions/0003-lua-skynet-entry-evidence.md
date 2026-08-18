# ADR 0003：Lua/Skynet 入口证据与只读 revision 边界

0.1.30 复核结论：本 ADR 的 builtin 入口提取方案已被 ADR-0002 的 CodeGraph-only 决策取代；Lua/Skynet 符号同样使用 CodeGraph 公开路径与名称引用。

- 状态：草案
- 来源提案：kp-9b39b72c2bcb0d23
- 创建审核人：codex

状态：草案

0.1.28 人工复核：当前 Builtin 引擎仍区分静态 Skynet 启动、协议派发和文件名推断入口；真实项目 harness 仍使用只读镜像、源目录前后快照以及 SVN/file-hash revision 证据。CodeGraph Adapter 接入没有扩大这些静态事实的证明边界，动态服务发现和运行时协议名仍需现场验证。

## 背景

真实 Lua/Skynet 项目需要在不写入源目录的前提下获得可追溯的启动入口、协议派发入口、范围风险和可重复 revision。静态分析不能证明动态服务发现、运行时协议名称或启动命令。

## 决策

1. BuiltinCodeIndexEngine 输出带路径、行号、来源符号、证据文本和置信度的入口记录，区分 Skynet 启动、协议派发和文件名推断入口。
2. 真实项目 harness 提供 dry-run，只输出选中文件、排除项、风险和入口，不创建镜像。
3. 有 SVN 时记录 SVN revision；没有 SVN 命令时使用选中文件路径与内容哈希组成稳定的 file_hash_only revision。
4. 入口知识页进入 generated Knowledge，可参与功能开发 context；文件名推断和动态运行时结论必须保留人工确认或 unknowns。
5. 只读镜像继续只复制允许索引的文件，并用源目录全树快照证明未写入源项目。

## 验收证据

- src/project_knowledge/engine.py
- src/project_knowledge/real_project.py
- src/project_knowledge/knowledge.py
- evaluation/real_project_harness.py
- tests/test_codegraph.py
- tests/test_retrieval_wp06.py

<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/real_project.py" -->
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="evaluation/real_project_harness.py" -->
<!-- project-kb:source file="tests/test_codegraph.py" -->
<!-- project-kb:source file="tests/test_retrieval_wp06.py" -->

## 0.1.32 当前执行结论

本 ADR 中由 `BuiltinCodeIndexEngine` 提取入口的历史方案已停止执行。当前 Lua/Skynet 框架事实由 `FrameworkIndex` 的 `lua-skynet` profile 基于 CodeGraph 公共符号和源码窗口生成：`skynet.start`/`skynet.dispatch` 作为入口或生命周期证据，`skynet.newservice`/`skynet.uniqueservice`/`skynet.register` 作为注册证据。动态服务发现、运行时协议名和反射调用继续保留为 unknown。

<!-- project-kb:source file="src/project_knowledge/frameworks.py" -->
<!-- project-kb:source file="tests/test_frameworks.py" -->
