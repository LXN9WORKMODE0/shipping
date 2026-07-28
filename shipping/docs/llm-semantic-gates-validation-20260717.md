# 论文分析语义门禁真实验证（2026-07-17）

## 人话结论

新门禁能在论文综合前发现 Evidence Unit 的 claim 超出逐字引文，也能在最终陈述发布前发现字段放错位置。工程链路已经工作，但现有 82 Card 论文的旧分析候选没有通过：99 条 Evidence Unit 中有 3 条确认存在真实引文缺口，50 条最终陈述中有 2 条 `review_uses` 只是复述论文事实。

因此，当前不能进入 250 Card 的章节摘要真实调用。否则新流程大概率会先在 Evidence claim 门禁停止，既无法验证章节摘要，也会产生没有新增结论的 API 成本。

## 本轮实现

- 新增 `llm.evidence_claim_support_review.v1`：逐条核验 Evidence Unit 的 claim 是否被本单元逐字引文直接支持。
- 新增 `llm.statement_role_review.v1`：逐条核验研究焦点、方法、发现、局限、综述用途和未决问题是否履行所在字段职责。
- 正常分析顺序固定为：证据抽取 → Evidence claim 门禁 → 论文综合 → 字段职责门禁 → 陈述证据支持门禁 → 发布。
- 任一门禁失败均保留原始响应、解析响应、Schema 结果、Token、失败账本和中文报告，不发布 `paper_analysis.json`。
- 新增只读命令 `llm-semantic-audit`，用于核验既有不可变修订候选；它不改写 Evidence Unit、陈述或处置结果。
- 运行合同升级为 `llm.analysis_run.v4`、`llm.statement_revision_run.v2` 和 `llm.paper_result.v3`。
- 修正 Windows 原子写入临时文件名过长问题，仍保持同目录原子替换。

全量自动测试：150 项，全部通过。

## 验证输入

- 论文：`考虑翻坝和天气的长江班轮运网鲁棒优化模型`
- 来源运行：`llm-v4-nonthink-large82-explicit-revision1-support-retry1-20260715`
- Card：82
- Evidence Unit：99
- 最终候选陈述：50
- Card 快照：`sha256:74f498f1382eb5d675bb817cd38231c4dd5e2260bdc9dc968d8f6e8b63f6d9aa`
- 候选分析：`sha256:6d3628c1e45e26ab993df11f142ed9881a35f4d5ffa1abeeb774be18ec2fe9e4`
- 模型：`deepseek-ai/DeepSeek-V4-Pro`，Non-think
- model profile：`sha256:20df525c0c69651672990eb47a515fe7eb7f367eca24a205c3115763f2865ee4`
- tokenizer revision：`0e1a0e5e52aea73055f50fef6f2423db370265b6`

预检中，Evidence claim 请求输入 18,677 tokens，字段职责请求输入 4,360 tokens；加最大输出和安全余量后分别为 94,213 和 30,744 tokens，均低于 1,000,000 tokens。

## 三次不可变审计

### 初版门禁

运行：`semantic-gates-large82-revision1-20260717`

- Evidence claim：87 直接支持、11 部分支持、1 不支持。
- 字段职责：47 一致、3 错位。
- 总用量：35,701 tokens。
- 逐条人工审视发现初版过度保守：多条引文明明直接写出 claim，仍因同义改述、多引文合并或 caveat 被判为部分支持。

### 第一轮校准

运行：`semantic-gates-large82-calibrated1-20260717`

- 字段职责稳定收敛为 48 一致、2 错位。
- Evidence claim 原始响应给出 92 直接支持、7 部分支持，但其中 3 行出现 `partially_supported + 空 unsupported_fragments`，且 reason 最后明确自我纠正为直接支持。
- Schema 以 `schema.evidence_claim_support_invalid` 拒绝整份响应；没有补齐、改 verdict 或采用部分结果。
- 总用量：35,969 tokens。

### 最终校准

运行：`semantic-gates-large82-calibrated2-20260717`

- 99/99 条 Evidence claim 和 50/50 条字段职责均完整返回，Schema 与覆盖校验通过。
- Evidence claim：95 直接支持、4 部分支持。
- 字段职责：48 一致、2 错位。
- 两道门禁均失败，正式论文分析不存在。
- 总用量：35,931 tokens。
- 两次响应均为 `finish_reason=stop`、reasoning 字符数为 0、计划/实际 prompt tokens 差值为 0。

## 最终阻断项

### 确认存在的 Evidence claim 缺口

1. `evidence_01fc50b402c2fe6788a27dd8`
   - 引文只写“拟研究、优化”，claim 写成“已构建模型并进行了实例验证”。
2. `evidence_2cfa29acd1f9bb83782918d3`
   - 引文只有式 (4.25)，claim 增加“期望收益度量最优性、罚函数度量鲁棒性”的术语解释。
3. `evidence_8fb59b5dc43fdf0ef6276f1e`
   - 引文只给 M3 最优值 4046 万元，claim 增加“相对差不到 5%，因此解鲁棒”。

### Evidence claim 保守误判

- `evidence_254a3f72533a3bf0d7a1f9c8`
  - 引文写“研究有很多”“研究越来越多”，claim 概括成“已有大量研究”。这是保守同义改述，当前模型仍判为部分支持。

该误判没有被代码放行。它作为人工可见阻断保留，说明门禁模型不是事实裁判本身，不能把模型 verdict 当作无需审核的真值。

### 确认存在的字段职责错位

两条 `review_uses` 都只复述论文构建了什么模型，没有说明这些模型怎样服务“三峡船舶积压与长江航运组织”综述，因而均被正确阻断：

- `statement_c3a7d9bebe0995e8e6ea6dbe`
- `statement_4161c8fb59316a5b926d81f9`

原先被判错位的模型天气假设，在明确“限制字段可以表达收窄范围的模型假设，字段职责不互斥”后，稳定判为字段一致。

## 当前边界

可以确认：

- 两道新门禁的输入隔离、Token 规划、Schema、全量覆盖、失败记账和发布隔离已经通过本地测试与真实 API 验证。
- 旧 Evidence Unit 中确实存在 claim 超出所选逐字引文的问题。
- 旧修订候选中确实存在 `review_uses` 字段漂移。

不能确认：

- 现有 82 Card 候选可作为严格自动发布结果。
- Evidence claim 核验模型可以完全代替人工裁决；本轮仍有 1/99 的明确保守误判。
- 250 Card 的章节摘要链路已经通过真实验证；本轮没有调用该论文。

下一步必须先决定失败项的正式处置合同：是增加一次显式 Evidence Unit/字段修订，还是保留严格失败并由人工裁决。这个选择会改变 evidence ID、下游重综合和人工审核职责，不能用静默改写或自动重试代替。

## 2026-07-17 后续：采用人工裁决合同

已选择并实现“人工裁决后一次性受约束处置”：人工只确认门禁是否成立，程序根据裁决收窄/删除 Evidence Unit 或重新综合字段，再重跑三道语义门禁。人工误判覆盖绑定原稳定 ID、verdict 和失败片段，不随新 ID 或新问题迁移。

本次真实审计已生成：

- `review/semantic_decision_guide.md`
- `review/semantic_decision_workbook.md`
- `review/semantic_decisions.csv`

2026-07-19 已把裁决界面升级为自包含证据工作单。每项直接展示 MinerU Markdown/Card 原文快照、相邻 Card、Evidence 输出、论文级字段上下文、门禁冲突与处理后果；CSV 使用相同编号和内容，内部 ID 只承担程序绑定。裁决表仍包含本报告的 4 个 Evidence claim 阻断和 2 个字段职责阻断，当前均为 `待裁决`。因此没有发起新的真实 API 调用；这不是程序失败，而是显式等待人工语义判断。完整自动测试为 158 项，全部通过。
