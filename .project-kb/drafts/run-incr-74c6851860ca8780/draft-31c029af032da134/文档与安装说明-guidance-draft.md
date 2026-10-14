<!-- project-kb:guidance-state:start -->
{"content_hash": "sha256:3e2085868d36cc306cece0df7237f8765a82c40bab28490d51666f3f4e8136be", "draft_id": "draft-31c029af032da134", "snapshot_id": "313f2910b5ad18e5c27ed3ee1e384033a42fdea6eee10c86333dc716c5b96226", "status": "awaiting_confirmation"}
<!-- project-kb:guidance-state:end -->

# 文档与安装说明

## 方法论引用

- 安装与文档维护方法（知识 ID：`methodology.documentation`）

## 当前项目事实指导

### 入口
- README.md

### 代码位置
- 项目根目录

### 调用流程
- 安装 CLI
- 初始化项目
- 验证 MCP
- 日常同步
- 升级或卸载

### 注册方式
- npm tarball 或 registry 安装

### 数据与配置
- 项目级 .codex/config.toml 和 .project-kb.yml

### 项目实施步骤
- 确认环境版本
- 安装 CLI
- 运行 init
- 运行 doctor
- 重启客户端

### 项目不变量
- 命令与 CLI help 一致
- registry E404 明确说明
- 项目卸载保留知识数据

### 项目测试
- 运行文档契约和 npm 验证

### 发布
- 同步 CHANGELOG 和版本

### 回滚
- 仅恢复 README 文档提交

## 变体
- 无

## 证据
- src/project_knowledge/__init__.py（sha256:f078d042eae6b1889cebd3615d4853375e7cf688cce5b21f04f18a99f8e3061f）

## 未确认事项
- 活动评测报告需独立更新
