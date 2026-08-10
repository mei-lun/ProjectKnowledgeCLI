# Model Provider 与 EvidencePack 指南

## 默认安全状态

Project Knowledge CLI 默认使用 `provider.id: disabled`、`provider.enabled: false`、`provider.allow_network: false` 和 `privacy.local_only: true`。初始化、同步、检索和知识生成不会因此发起任何网络请求。

`project-kb generate --dry-run` 只在本机读取明确列出的相对文件，构建经过限制和脱敏的 EvidencePack，然后显示字段、文件、估算 Token、排除文件、脱敏统计和策略阻断原因。dry-run 永远不会调用 Provider。

```bash
project-kb generate "新增背包物品使用功能" \
  --project /path/to/project \
  --file service/bag.lua \
  --file service/player.lua \
  --dry-run \
  --json
```

绝对路径、越过项目根目录的路径和符号链接逃逸都会在读取前拒绝。`.env`、私钥、凭据目录等高风险路径默认整文件排除；普通源码中的密码、Token、Bearer 凭据、已知 API Token 和私钥块会替换为 `[REDACTED:<kind>]`。

## EvidencePack 契约

EvidencePack 使用 `evidence-pack-v1` Schema，包含：

- 中文任务；
- 相对文件路径；
- 脱敏后的正文及其 SHA-256；
- 每个文件的估算 Token 和脱敏位置；
- 被路径、文件数或 Token 限制排除的文件及原因；
- 可选源码提交；
- 对规范化完整证据包计算的稳定 SHA-256。

相同任务、来源提交、文件内容和限制会得到相同 `pack_hash`。包中不保存绝对项目路径或 Secret 原值。

## Provider 类型

### disabled

默认 Provider。执行生成会明确失败，dry-run 仍可使用。

### fake

用于离线测试结构化生成、Schema 拒绝、缓存和检查点。启用示例：

```yaml
provider:
  id: fake
  model: fake-v1
  enabled: true
  allow_network: false
```

Fake Provider 不访问网络，不能作为真实语义质量证据。

### http-json

标准库实现的结构化 HTTP Provider。最小本地配置：

```yaml
privacy:
  local_only: true

provider:
  id: http-json
  model: local-model
  endpoint: "http://127.0.0.1:11434/generate"
  enabled: true
  allow_network: true
  api_key_env: ""
```

服务接收 JSON object，返回：

```json
{
  "output": {"任意结构化字段": "值"},
  "usage": {"input_tokens": 100, "output_tokens": 20}
}
```

非本机 endpoint 还必须同时满足：

1. `privacy.local_only: false`；
2. HTTPS；
3. `provider.enabled: true`；
4. `provider.allow_network: true`；
5. `provider.authorization: I_AUTHORIZE_REDACTED_SOURCE_CODE_TRANSFER`。

API 密钥只通过 `api_key_env` 指定的环境变量在执行瞬间读取；配置、preview、缓存和检查点都不保存密钥值。真实云模型、源码允许外发范围和部署位置仍必须通过审计决策 D-001/D-002 确认。

## 执行保证

- Provider 输出必须先脱敏，再通过调用方给出的 JSON Schema；失败结果不进入缓存。
- 缓存键包含 EvidencePack、Provider、模型、提示词和输出 Schema 版本。
- 缓存命中仍重新验证输出 Schema。
- 检查点只记录哈希、版本、状态和错误类型，不记录证据正文或凭据。
- HTTP Provider 支持超时和有限重试；取消信号会在执行前及重试间检查。
- Secret 检测属于纵深防御而不是完备证明；启用外发前必须人工查看 dry-run，并使用高风险路径拒绝策略缩小范围。
