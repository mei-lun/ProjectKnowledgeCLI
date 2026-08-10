# Proposal 审核与知识提升指南

Proposal 是语义草案进入人工维护知识前的强制审核边界。系统不会因为模型生成成功或来源格式校验通过，就直接修改 `docs/knowledge/curated/` 或已有 ADR。

## 生命周期

```text
源码同步
  -> Semantic Update Queue
  -> Feature Guide draft 或手工结构化 Patch
  -> pending Proposal
  -> apply（applied）/ reject（rejected）/ 来源或目标变化（conflicted）
```

- `pending`：只写入 `.project-kb/proposals/<id>.json`，目标文档保持不变；
- `applied`：审核通过，精确应用到单一目标；重复 apply 不会再次修改；
- `rejected`：保留审核人、时间和理由，不删除审计记录；
- `conflicted`：目标或来源哈希变化，旧提案被冻结，必须重新生成。

Proposal ID 由目标、目标哈希、理由、来源哈希、operation、置信度和变更范围的规范化 JSON 计算，相同输入得到相同 ID。

## 从 Feature Guide 草案创建提案

```bash
project-kb propose HEAD --project /path/to/repository --draft bag-item-use --dry-run --json

project-kb propose HEAD --project /path/to/repository --draft bag-item-use --json
```

系统会再次校验结构化 Feature Guide，收集草案和全部源码引用的哈希，并把中文 Markdown 包装为稳定的 `feature-bag-item-use` generated block。此时 curated 文件仍不会创建或修改。

## 手工创建 generated block 提案

```bash
project-kb propose HEAD --project /path/to/repository --target docs/knowledge/curated/architecture.md --reason "认证入口已经迁移" --evidence src/auth/service.py --operation upsert_generated_block --block-id auth-entrypoints --content-file /tmp/auth-entrypoints.md --json
```

允许的 operation：

- `upsert_generated_block`：新建或替换指定 generated block，保留所有人工段落；
- `delete_generated_block`：只删除指定 generated block，必须同时给出 `--deleted-source` 和 `--supersedes`；
- `append_adr_draft`：只允许写入尚不存在的 `docs/knowledge/decisions/*.md`，并强制标为中文“草案”。

删除示例：

```bash
project-kb propose HEAD --project /path/to/repository --target docs/knowledge/curated/architecture.md --reason "旧认证入口已经删除并由新入口替代" --evidence src/auth/new_service.py --operation delete_generated_block --block-id legacy-auth-entrypoint --deleted-source src/auth/legacy_service.py --supersedes feature.new-auth-entrypoint
```

ADR 示例：

```bash
project-kb propose HEAD --project /path/to/repository --target docs/knowledge/decisions/0002-auth-boundary.md --reason "记录新的认证边界" --evidence docs/knowledge/curated/architecture.md --operation append_adr_draft --content-file /tmp/0002-auth-boundary.md
```

已有 ADR 无论状态如何都不能通过 Proposal 改写。废弃旧决策时应创建新的 ADR 草案并通过 `--supersedes` 声明替代关系。

## 审核、预览、应用和拒绝

```bash
project-kb apply kp-0123456789abcdef --project /path/to/repository --reviewer mei --reason "已核对源码、测试和业务约束" --dry-run --json

project-kb apply kp-0123456789abcdef --project /path/to/repository --reviewer mei --reason "已核对源码、测试和业务约束"

project-kb reject kp-fedcba9876543210 --project /path/to/repository --reviewer mei --reason "来源不足，暂不提升"
```

`apply --dry-run` 返回目标 diff，但不写目标或审计状态。正式 apply 前会重新计算目标和全部本地来源哈希；任何变化都会使提案进入 `conflicted`。apply 只允许写 Proposal 的单一目标文件，curated 目标只允许操作明确标记的 generated block。

所有三个写命令都支持 `--dry-run`、`--json` 和 `--quiet`。应用后运行 `project-kb sync`，让清单和检索索引读取最新文档；人工审核仍应检查生成内容是否真正被引用语义支持。
