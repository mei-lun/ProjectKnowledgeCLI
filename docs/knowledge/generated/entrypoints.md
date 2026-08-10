<!-- 本文件由 project-kb 自动生成，请勿手动编辑。 -->

# Lua/Skynet 入口证据

本页只记录源码中可定位的启动和协议派发证据。动态启动命令、运行时生成的协议名和服务发现结果必须现场验证。

| 类型 | 来源符号 | 目标 | 源码位置 | 置信度 |
| --- | --- | --- | --- | ---: |
| - | 未检测到静态入口 | - | - | - |

## 使用约束

- “Skynet 启动”表示检测到 skynet.start 或 skynetx.start。
- “协议派发”表示检测到 skynet.dispatch、protocol.dispatch、protocol.run 或 protocol.exec。
- “文件名推断入口”只表示文件命名线索，不是已验证的启动入口。
