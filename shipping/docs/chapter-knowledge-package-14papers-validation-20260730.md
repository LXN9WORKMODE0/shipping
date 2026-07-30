# 14篇论文 Chapter Knowledge Package 验收记录

## 验收对象

- Framework：`framework-14papers-20260730-v2`
- 模型配置：`deepseek-v4-pro-official`
- 写作配置：`config/review-writing-default.json`
- 上下文窗口：1,000,000 Token
- 章节最大输出：32,768 Token
- 安全余量：10,000 Token

知识包构建是本地确定性阶段，本次未向外部服务发送论文内容。

## 最终写作请求合同

Task 8 的“真实Token预算”必须基于后续章节写作实际使用的消息。因此在实现知识包前先冻结：

- `REVIEW_CHAPTER_SYSTEM_PROMPT`
- `llm.review_chapter.v1` 动态输出Schema
- `chapter_max_output_tokens`
- citation key允许集合

预算公式：

```text
完整messages输入Token
+ chapter_max_output_tokens
+ safety_margin_tokens
<= context_window_tokens
```

## 9节真实结果

| 章节 | 论文数 | 输入Token | 含输出和余量的预留Token | 状态 |
|---:|---:|---:|---:|---|
| 1 引言 | 14 | 478,750 | 521,518 | 通过 |
| 2 根本原因辨析 | 2 | 21,574 | 64,342 | 通过 |
| 3 船型与尺度 | 5 | 54,641 | 97,409 | 通过 |
| 4 调度与组织 | 5 | 171,912 | 214,680 | 通过 |
| 5 工程挖潜与新通道 | 5 | 255,537 | 298,305 | 通过 |
| 6 智能化与信息系统 | 7 | 204,710 | 247,478 | 通过 |
| 7 运输组织与分流 | 3 | 234,015 | 276,783 | 通过 |
| 8 监测与基础数据 | 3 | 34,207 | 76,975 | 通过 |
| 9 结论 | 14 | 479,143 | 521,911 | 通过 |

最大请求为第9节，占1M上下文窗口约52.2%。当前14篇语料不需要拆分、截断、摘要或降级。

## 中等章节审视

第5节包 `package-framework-v2-section-5-20260730` 包含：

- 5篇完整规范化Markdown
- 491张Card投影
- 21条Evidence
- 5份Paper Understanding
- 5条完整题录及引用Key
- 2个Landscape维度
- 3条双方论文均在本节的关系
- 1项显式语料缺口
- 42个冻结来源文件哈希

两篇长论文的 leave-one-out 边际Token约为107k和114k。全文与Card会重复承载部分文字，但这是计划明确要求的完整上下文，且当前预算充足，因此不做压缩。

## 来源和失败边界

每个包只接受显式Framework run与单个section。程序重放：

1. Framework manifest、冻结来源、原始响应和正式输出。
2. Landscape及其显式Understanding collection。
3. 每篇Understanding的完整Markdown、Card、投影、Evidence和正式理解对象。
4. 题录与Framework citation key。

Evidence quote必须是对应Card extract的连续子串，原文行号必须位于Card来源范围。任一来源、哈希、身份、引用或预算失败时不发布 `output/knowledge_package.json`；候选完整输入与失败账本保留。

## 结论

Chapter Knowledge Package Task 8通过。当前架构可以按“每章一次、只读本章知识包”进入Task 9章节写作，不需要跨章共享模型上下文。
