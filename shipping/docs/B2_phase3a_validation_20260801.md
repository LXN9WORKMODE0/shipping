# B2 Phase 3A：受约束章节写作与草稿装配验收报告

日期：2026-08-01

分支：`codex/b2-review-pipeline`

验证范围：14 篇真实论文、8 个引言及正文章节、DeepSeek V4 Pro 非思考模式

## 1. 定性结论

B2 Phase 3A 已达到“受约束章节写作链路可以进入语义审计”的工程验收条件：

- 一章对应一次隔离的写作任务，不跨章混合生成；
- 程序先确定每段必须实现的 Claim、引用、限定语和禁止表述，模型只负责写正文；
- 所有核心 Claim 必须实现，支持 Claim 可以不使用但必须记入遗漏清单；
- 每段声明的 Claim 和引用必须与程序计划完全一致；
- 章节运行采用不可变 run ID，原始响应、冻结输入、Schema、配置、输出和失败均可追溯；
- 8 个非结论章节均已在同一最终契约下生成并通过确定性重放；
- 组装器只接受 Framework B2 顺序下完整的引言及正文集合；
- 结论在 API 调用前被明确阻断，当前草稿始终标记为非正式产物。

这里通过的是“写作边界、机械一致性、运行隔离和可重放性”，不是最终综述质量。
计划外事实、事实写大、指代歧义和跨段重复尚未经过语义模型审计，因此当前产物不能作为
正式综述发布。

## 2. 本阶段实现

### 2.1 写作输入合同

新增 `llm.review_writing_input_b2.v1`。每个输入由 Phase 2 章节知识包和
Framework B2 确定性派生，包含：

- 章节任务和全文纲要；
- Approved Claim Ledger；
- 必须实现的核心 Claim 和可选支持 Claim；
- 与 Claim 对应的 Source Window、Paper Understanding 投影和题录；
- 章节字数、段落数、Claim 容量和表述强度约束；
- 程序生成的逐段 Claim、引用、限定语和禁止表述计划。

引言和正文的 `writing_ready=true`。结论固定为 `writing_ready=false`，原因是
`conclusion_requires_audited_core_claims`。

### 2.2 程序规划、模型写作

调试初期曾让模型自行决定 Claim 的段落归属，但真实调用反复出现以下问题：

- 核心 Claim 遗漏；
- 使用了支持 Claim 的内容，却没有声明对应 Claim ID；
- Claim 和 citation key 集合不一致；
- 调整拆段后，同一事实的标识和人工判断对象不稳定。

最终职责划分调整为：

1. 程序按 Claim 重要性、类型、引用重叠和段落容量选择并分配 Claim。
2. 程序冻结每段精确的 Claim ID 和 citation key 数组。
3. JSON Schema 使用 `prefixItems` 和 `const` 固定段落序号、Claim 和引用。
4. 模型只填写正文文本，不再承担结构规划。
5. 程序在模型返回后重新检查全部合同。

这使人工审核对象不再随模型的拆段选择漂移，也避免把关键控制逻辑藏在 Prompt 中。

### 2.3 长度策略修正

真实调用暴露出，固定字数并不是可靠的内容质量代理。

第一版把章节最低字数机械均分为每段最低字数，导致第 7 节总长 797 字、已经超过章节
下限 639 字，仍因第二段为 286 字而被误拒。第 4 节也多次因单段略短失败。

最终策略为：

```text
章节下限 = min(目标字数的 60%, max(章节类型底线, 计划 Claim 字数的 120%))
章节上限 = 目标字数的 120%
段落硬下限 = 章节平均下限的 60%
段落生成目标 = 章节目标字数 / 段落数
```

章节总下限、总上限仍是硬门禁。段落下限只防止空洞短段，不再强迫所有段落等长。
Claim 完整性、引用一致性、限定语和禁止表述没有随长度策略放松。

### 2.4 单章不可变运行

新增 CLI：

```text
llm-review-writing-b2
```

每章运行保存：

- 冻结写作输入；
- 动态 JSON Schema；
- 模型配置和写作配置；
- Prompt 哈希和 Token 计划；
- 原始供应商响应、`finish_reason` 和 Token 用量；
- 经验证的章节 JSON 和中文审核稿；
- 失败账本和最终 manifest。

失败运行不会覆盖成功运行，也不会发布部分有效章节。加载器会重新派生输入并验证输出、
稳定 ID 和文件哈希。

### 2.5 Phase 3A 草稿装配

新增 CLI：

```text
assemble-review-b2-phase3a
```

组装器要求：

- 所有章节来自同一个 Framework B2；
- 必须按框架顺序提供全部引言和正文章节；
- 章节均处于 `semantic_unplanned_claim_audit=not_run` 状态；
- 题录 key 不得冲突；
- 不生成结论，不发布正式综述。

正式组装运行：

```text
review-b2-phase3a-14papers-20260801-v1
```

状态为 `completed`，但 `formal_review_published=false`，结论状态为
`not_ready_requires_audited_core_claims`。

## 3. 最终真实运行结果

最终统一契约的章节运行如下：

| 节 | 最终 run ID | 正文字符 | 已批准 Claim | 已实现 Claim | 核心 Claim | 未用支持 Claim |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `writing-b2-14papers-section-1-20260801-v6` | 358 | 3 | 3 | 2 | 0 |
| 2 | `writing-b2-14papers-section-2-20260801-v6` | 554 | 8 | 6 | 4 | 2 |
| 3 | `writing-b2-14papers-section-3-20260801-v7` | 859 | 8 | 6 | 4 | 2 |
| 4 | `writing-b2-14papers-section-4-20260801-v8` | 712 | 10 | 6 | 4 | 4 |
| 5 | `writing-b2-14papers-section-5-20260801-v4` | 1,037 | 11 | 6 | 4 | 5 |
| 6 | `writing-b2-14papers-section-6-20260801-v4` | 709 | 9 | 6 | 4 | 3 |
| 7 | `writing-b2-14papers-section-7-20260801-v6` | 827 | 6 | 6 | 4 | 0 |
| 8 | `writing-b2-14papers-section-8-20260801-v4` | 923 | 7 | 6 | 4 | 1 |

合计：

- 正文 5,979 字符；
- 实现 45 条 Claim，其中核心 Claim 30 条；
- 核心 Claim 遗漏为 0；
- 最终 8 次调用输入 408,651 Token，输出 6,200 Token；
- `finish_reason` 均为 `stop`；
- 使用非思考模式，`reasoning_content_chars=0`。

第 1 节单次输入为 123,584 Token，明显高于其他章节。这说明当前 Source Window 投影仍有
压缩空间，但不影响本阶段合同验收；应在语义审计证明信息足够后再优化输入，不应提前删减来源。

## 4. 失败账本与修正过程

本阶段共保留 46 个章节运行：

- 完成 28 个，包括中间契约下的历史成功产物；
- 失败 18 个；
- 其中 45 个实际调用了 API，结论门禁失败未调用 API；
- 全部尝试累计输入 2,280,330 Token，输出 33,213 Token。

失败类型：

| 错误代码 | 次数 | 暴露的问题 |
| --- | ---: | --- |
| `writing_b2.paragraph_length_out_of_range` | 10 | 机械均分段落长度造成误拒，模型也会把最低值当目标值 |
| `writing_b2.chapter_length_out_of_range` | 4 | 70% 目标字数下限对稀疏章节过强 |
| `writing_b2.paragraph_citation_mismatch` | 2 | 模型自行规划时会错配 Claim 与引用 |
| `writing_b2.required_qualifier_missing` | 1 | 模型可能省略语料范围限定语 |
| `writing_b2.conclusion_not_ready` | 1 | 结论在 API 调用前按设计阻断 |

失败记录没有删除，也没有通过读取旧成功产物兜底。每次实质重试都使用新的 run ID；契约变化
后，全部 8 节重新生成统一代际，避免新旧策略混装。

## 5. 当前草稿的人工审视

当前正文已经具备可读的主题结构、段落级引用和跨论文比较，明显强于直接拼接 Evidence。
但只读审视仍发现需要语义审计处理的问题：

1. 第 5 节“将两者与 2013 年研究对照……后者以 2018 年数据为基础”存在指代不清。
2. 第 7 节出现“建议未来……”的规范性扩展，需要确认它是否对应批准的
   `normative_recommendation` Claim，而不是模型新增意见。
3. “根本举措”“只能依赖”“关键作用”等强表述虽然可能来自作者观点，仍需逐句确认限定语和
   归因是否保留。
4. 第 1、2、5、8 节复用了部分统计事实，属于跨节重复风险，机械 Claim 检查尚不能判断其
   是否影响综述节奏。
5. 当前验证只能证明正文声明了计划内 Claim，不能证明正文每个事实都可由声明的 Claim 覆盖。

因此，当前草稿适合作为 Phase 3B 的审计输入，不适合作为与“直接把 PDF 交给模型”进行最终
质量对比的成品。

## 6. 测试结果

B2 Phase 3A 定向测试：

```text
11 passed
```

覆盖范围包括：

- 核心 Claim 完整性与跨段去重；
- Claim 与引文集合一致性；
- 必需限定语；
- 非等长段落与章节总长度门禁；
- 单章发布和加载器确定性重放；
- 结论在调用 API 前失败；
- 完整章节集合和框架顺序；
- 组装发布和加载器确定性重放；
- 两个独立 CLI 入口。

项目全量测试：

```text
492 passed, 73 subtests passed
```

Python 编译检查通过。

## 7. 下一步

进入 B2 Phase 3B：写后语义 Claim 审计。

最小实施顺序：

1. 审计单位保持为单章，输入章节正文、实际实现的 Claim 和对应 Source Window。
2. 审计模型逐句标记 `supported`、`overstated`、`unplanned`、`ambiguous_attribution` 和
   `citation_scope_error`，每个问题必须给出段落 ID、原句和相关 Claim ID。
3. 程序检查审计输出的段落、Claim 和引用身份，不接受模型创造的 ID。
4. 只重写有问题的段落，重写时仍不得增加 Claim；无问题段落保持字节不变。
5. 重写后再次执行机械合同和语义复审，保存前后差异与未解决问题。
6. 从通过审计的核心 Claim 构建结论输入，再单独生成并审计结论。
7. 完成正式装配后，再与 B1 和直接全文 Prompt 进行匿名质量评价。

当前不需要人工逐条裁决。只有当同一语义争议在审计、定向重写和复审后仍无法自动闭合时，
才生成少量带原文窗口、Claim 和前后文本的人工裁决项。
