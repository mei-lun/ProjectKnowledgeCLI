<!-- project-kb:guidance-state:start -->
{"content_hash": "sha256:8b241d77474675f62f4ea4a71113331afeb5d62aa8448475acca7f679f913483", "draft_id": "draft-2ed9a4f31c713faa", "snapshot_id": "78089c25aa03ace72a036f25ef18f68116df04749150dc3d9f19f0c36f104bd3", "status": "awaiting_confirmation"}
<!-- project-kb:guidance-state:end -->

# 版本与变更日志项目事实指导

## 方法论引用

- 版本与变更日志方法论（已有草稿，尚未审核）（知识 ID：`draft-e060e6df0b570a91`）

## 当前项目事实指导

### 入口
- scripts/bump_version.py 以项目 src 目录加入导入路径后调用 project_knowledge.versioning.main；接受中文 message、--project 和 --dry-run。

### 代码位置
- src/project_knowledge/__init__.py 定义 __version__，本次值为 0.1.66。
- src/project_knowledge/versioning.py 定义读版本、递增补丁、写变更日志和插件版本的实现。

### 调用流程
- 入口脚本 → main 解析参数 → bump_patch_version → read_project_version → next_patch_version → _replace_version / _prepend_changelog_entry → atomic_write。

### 注册方式
- 版本维护由独立脚本显式执行；没有在此模块中注册自动递增的文件监听器或 Git hook。

### 数据与配置
- 版本文件必须含匹配 major.minor.patch 的 __version__ 声明。
- 脚本更新 CHANGELOG.md；仅在 plugins/project-knowledge/.codex-plugin/plugin.json 存在且含 version 字段时同步插件版本。

### 项目实施步骤
- 读取当前版本与既有变更，确认本批尚未递增。
- 可先运行 python scripts/bump_version.py "中文说明" --dry-run 预览。
- 本批仅执行一次实际递增，随后检查 CLI 版本、CHANGELOG 和插件版本。
- 本次目录整理从 0.1.65 递增至 0.1.66；知识同步不再次递增。

### 项目不变量
- next_patch_version 保持 major/minor，只将 patch 加一。
- 空白变更说明、非法版本格式、重复 changelog 版本以及存在却无 version 字段的插件清单都会抛错。
- dry_run 返回旧/新版本但不执行文件写入。
- 脚本不会重写评测报告或将其历史结果变为当前版本结果。

### 项目测试
- 版本回归 tests/test_versioning.py 在本次定向 pytest 中通过。
- 本次 61 项定向 Python 检查中 59 项通过；2 项失败都来自清理前已存在的活动报告 0.1.62 与包版本不一致。
- Node 安装器/运行时 18 项通过、1 项平台跳过；0.1.66 wheel 与 npm staging 构建及版本检查通过。

### 发布
- 每个文件通过 atomic_write 写入；多文件发布仍需逐项验证一致性。
- 应从当前源码构建制品，核对 wheel 元数据与 npm 清单版本；本次只完成构建验证，未发布或提交 Git。

### 回滚
- 回滚只恢复本批版本源、插件清单与新增 changelog 条目，保留之前未提交修改；不得对整个工作区执行硬重置。
- 代码中无自动跨文件回滚入口，异常后需先核验三个输出文件再决定重试。

## 变体
- --dry-run 为预览，不写文件。
- --project 可指定项目根目录。
- 缺少插件清单时只更新版本声明与 CHANGELOG。

## 证据
- src/project_knowledge/__init__.py（sha256:33b49821d451e0aac126317e5ca71f74ea0caf57da50dbfa69d51ab70da2159b）
- src/project_knowledge/versioning.py（sha256:28c1eb70f34cef82854e409a23843cfe22a0c29eff5595dab889fd32b2f1cedb）
- scripts/bump_version.py（sha256:84f254dec8f3c3e1fd1dd58d995a675db61fd03839c215cf77733ad41c0c821d）

## 未确认事项
- 引用的方法论与本次分类/指导均等待用户审核，不能当作 verified 知识。
- 活动评测报告尚未对齐当前源码，完整测试和发布质量门未通过或未完成；本次清理没有伪造评测结果。
