# ADR 0003：Lua/Skynet 入口证据与只读 revision 边界

- 状态：草案
- 来源提案：kp-9b39b72c2bcb0d23
- 创建审核人：codex

状态：草案

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
- tests/test_wp02_evidence.py
- tests/test_wp02_knowledge.py

<!-- project-kb:source file="src/project_knowledge/engine.py" -->
<!-- project-kb:source file="src/project_knowledge/real_project.py" -->
<!-- project-kb:source file="src/project_knowledge/knowledge.py" -->
<!-- project-kb:source file="evaluation/real_project_harness.py" -->
<!-- project-kb:source file="tests/test_wp02_evidence.py" -->
<!-- project-kb:source file="tests/test_wp02_knowledge.py" -->
