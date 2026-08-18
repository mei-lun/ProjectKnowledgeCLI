# RT-003 可选向量检索设计

## 目标

在不改变默认检索行为、不引入默认网络请求的前提下，为 Project Knowledge CLI 增加可插拔向量召回。向量能力只补充 lexical 与 CodeGraph 候选，不能覆盖路径、精确符号或结构关系证据。

## 配置契约

- `retrieval.embeddings: disabled`：默认值，不创建 provider，不读写向量表，现有结果保持不变。
- `retrieval.embeddings: local`：启用确定性的本地 provider，用于离线索引、测试与可复现评测。
- 其他值明确报告 `unsupported_embeddings`，不静默回退为已启用。
- 本批不复用语义生成功能的 HTTP Provider 配置，也不允许向量检索发起网络请求。

## 组件

### EmbeddingProvider

Provider 暴露稳定元数据 `provider_id`、`model_id`、`dimension`，以及批量 `embed(texts)`。实现必须保证返回数量和维度与声明一致；异常、维度错误或不可用状态由调用方记录为 fallback，不得破坏 lexical 检索。

首个生产实现为 `DeterministicLocalProvider`：按规范化词项和稳定哈希生成固定维度向量，不依赖第三方模型或网络。它只提供可插拔链路的真实本地行为，不宣称具备神经语义模型质量。

### VectorIndex

向量存储在现有 `.project-kb/index.db` 中，新增：

- `vector_documents`：文档 ID、kind、内容哈希、provider/model、维度、向量、更新时间；
- 必要的 provider/model/hash 索引。

文档范围为 KnowledgeRecord 的标题、标签和正文。索引键为知识 ID；内容哈希、provider ID、model ID 或维度任一变化即重建该记录。删除 KnowledgeRecord 时同步删除向量记录。禁用时不执行同步。

### 检索集成

`KnowledgeAPI.search` 先执行现有 lexical 查询，再在启用且 provider 可用时查询向量索引。结果按知识 ID 合并：

- lexical 分数和现有可信度、新鲜度权重保持主排序基础；
- 向量相似度是有上限的补充信号，只能补充候选或解决同级候选排序；
- lexical 明确命中的记录不能被纯向量候选压过；
- 路径、符号、CodeGraph 结构文件排序仍由现有 `policy-v1` 决定。

返回值增加 `vector_retrieval` 诊断，包括 enabled、provider、model、indexed、candidate_count、fallback、fallback_reason 和 duration_ms。禁用状态必须显示 enabled=false 且不加载 provider。

## 生命周期

`ProjectService.initialize/rebuild/sync` 在生成 KnowledgeRecord 后调用向量同步。同步只处理内容哈希或 provider 元数据变化的记录，并删除已消失记录。provider 不可用时保留现有 lexical 索引，状态和检索诊断明确显示 fallback。

## 错误与安全边界

- 默认禁用和 local provider 均零网络请求。
- provider 构造、嵌入、维度校验或向量解码失败时，检索继续返回 lexical 结果。
- 损坏的单条向量不影响其他记录，错误作为 fallback 原因暴露。
- 向量表不是 CodeGraph 事实来源，不参与影响分析或符号身份判断。

## 测试与验收

先写失败测试，覆盖：

1. disabled 零 provider 加载、零向量表写入、结果与 lexical 一致；
2. local provider 输出确定、维度稳定；
3. 内容哈希、模型、维度变化触发重建；
4. 删除知识记录同步删除向量；
5. provider unavailable 和非法维度明确 fallback；
6. 混合结果可复现，lexical 精确命中优先；
7. 现有 50 题 hybrid precision 不低于关闭向量时的同提交基线；
8. 状态记录索引数量、fallback 次数和耗时，不发送遥测。

完成后运行聚焦测试、全量 pytest、真实 CodeGraph Adapter 验证、CI 工作流验证和同提交 on/off 检索对照。只有上述行为、文档、版本 `0.1.33` 与知识同步全部完成后，RT-003 才标记为“已完成基础版”。

## 非目标

- 不接入云端 embedding API。
- 不下载第三方模型。
- 不更改 CodeGraph Adapter 或恢复 builtin parser。
- 不用向量分数替代 `policy-v1` 文件排名。
- 不因现有绝对质量门失败而降低阈值。
