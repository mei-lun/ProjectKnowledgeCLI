# Provider 与 EvidencePack

0.1.28 人工复核：当前 Proposal Schema、CLI 和 Provider 配置仍不改变外发授权、EvidencePack 脱敏和草案落库前校验边界；Feature Guide 草案转 Proposal 时只读取已经本地校验并持久化的草案与来源哈希，不会新增网络请求。发布最终化和 CodeGraph Adapter 也不绕过这些边界。

## 职责

`EvidencePackBuilder.build` 把明确指定的项目内相对文件转换为有上限、可审计的模型输入。它按路径排序以保持确定性，先拒绝 `.env`、私钥和凭据目录等高风险路径，再通过 `SecretScanner.redact` 脱敏正文，最后应用文件数与 Token 上限。`EvidencePack.to_dict` 必须通过 `EVIDENCE_PACK_SCHEMA`，`pack_hash` 对不含自身哈希字段的规范 JSON 计算。

`ModelProvider.generate_structured` 是模型扩展点。当前支持：

- `DisabledProvider`：默认状态，执行时明确失败；
- `FakeProvider`：完全离线，用于 Schema、缓存、检查点和后续 Feature Guide 测试；
- `HttpJsonProvider`：显式授权的结构化 HTTP 接口，支持超时、有限重试和取消检查。

`ModelRuntime.generate` 将 EvidencePack、输出 Schema 和版本元数据组成请求。Provider 输出先递归脱敏，再通过调用方 Schema 和可选的调用方语义校验；只有两层校验均成功的结果进入缓存。缓存命中仍重新执行 Schema 与调用方语义校验。检查点只保存请求/证据哈希、Provider、模型、提示词、Schema 版本、状态和错误类型。

## 安全不变量

1. 默认 `provider.id: disabled`、`enabled: false`、`allow_network: false`、`local_only: true`，不会发生网络请求。
2. `generate --dry-run` 使用不可执行的 preview Provider，只列出字段、相对文件、Token、脱敏统计、排除文件和策略问题。
3. 本机 loopback HTTP 必须显式启用 Provider 和网络；非本机 HTTP 还必须关闭 local_only、使用 HTTPS 并提供精确外发授权短语。
4. endpoint 禁止内嵌用户名或密码。API 密钥只从配置指定的环境变量读取，不进入 preview、缓存或检查点。
5. 绝对路径、`..` 越界和符号链接逃逸在文件读取前拒绝。
6. Provider 返回的敏感字段或已知 Token 即使来自模型响应，也必须在验证和持久化前脱敏。

## 新增 Provider 的开发步骤

1. 实现 `ModelProvider.generate_structured`，返回 object 类型的结构化输出和 Token 使用量。
2. 在 `create_provider` 与 `create_preview_provider` 中注册真实实现和不可执行预览描述。
3. 在 `provider_policy_issues` 中声明所有执行授权前置条件。
4. 不在配置、异常、日志、缓存或检查点中记录凭据值与未脱敏证据。
5. 增加 disabled、授权拒绝、dry-run、成功、超时、重试、取消、非法 Schema 和 Secret 泄漏测试。
6. 更新 Provider/模型/提示词/输出 Schema 版本，并重跑当前 40 题质量门。

## 当前边界

当前 HTTP JSON 协议是通用本地/云端传输边界，不代表已经选择真实生产模型。云部署位置和源码允许外发范围仍等待 D-001/D-002。Feature Guide 输出 Schema、二次来源校验和 draft 落库已由 WP-04 完成；WP-05 允许草案经显式审核进入 curated generated block，但不自动证明语义正确。

<!-- project-kb:source file="src/project_knowledge/proposal.py" -->

<!-- project-kb:source file="src/project_knowledge/evidence.py" -->
<!-- project-kb:source file="src/project_knowledge/provider.py" -->
<!-- project-kb:source file="src/project_knowledge/config.py" -->
<!-- project-kb:source file="src/project_knowledge/cli.py" -->
<!-- project-kb:source file="src/project_knowledge/models.py" -->
<!-- project-kb:source file="src/project_knowledge/schemas.py" -->
<!-- project-kb:source file="src/project_knowledge/semantic.py" -->

<!-- project-kb:generated id="wp08-provider-compatibility" -->
## WP-08 配置兼容补充

Provider 配置属于 config-v1 Schema 的可扩展对象。v0→v1 迁移不会删除自定义 Provider 字段；高于 v1 的配置由旧程序显式拒绝，避免静默误解网络授权。当前正式 Provider ID 仍只有 disabled、fake、http-json，客户端适配不改变 Provider 的网络授权或证据外发边界。
<!-- /project-kb:generated -->
