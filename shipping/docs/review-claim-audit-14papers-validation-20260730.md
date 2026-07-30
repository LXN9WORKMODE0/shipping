# 14篇论文综述正文Claim审计验证记录

## 结论

Task 10 已建立可运行、可重放、可续跑的写后审计链，但当前正文
`writing-14papers-20260730-v2` 未通过发布门禁。

最终接受运行：

- 审计运行：`claim-audit-14papers-20260730-v12`
- 状态：`completed`
- 可发布：否
- 章节：9/9
- Claim：287
- 事实Claim：230
- 综述内部综合Claim：52
- 导航Claim：5
- supported：196
- qualified：21
- unsupported：13
- 核心unsupported：11
- result type写大：14
- validation level写大：14
- 输入Token：1,990,000
- 输出Token：63,385
- 合计Token：2,053,385

人工风险报告位于：

`workspace/_review_claim_audits/runs/claim-audit-14papers-20260730-v12/review/claim_audit_report.md`

每章完整机器审计位于：

`workspace/_review_claim_audits/runs/claim-audit-14papers-20260730-v12/chapters/section-XXX/validated_audit.json`

## 技术实现

1. 程序确定性拆分章节句子并生成稳定Claim ID，模型不复制Claim文本或生成ID。
2. 模型只返回Claim类型、重要性、支持状态、来源层级和写大检查。
3. 程序依据段落引用和来源层级，从冻结知识包确定性展开Evidence、Card或完整Markdown。
4. `factual`、`review_synthesis` 和 `navigation_synthesis` 分开统计。
5. 只有事实Claim进入支持率、结果类型、验证水平和核心不支持发布门禁。
6. 模型返回的越界引用、缺失来源和非法状态转换均显式记录在
   `normalization_flags`，不静默视为支持。
7. 人工Markdown报告只展开需关注Claim，每个来源最多预览3条；完整来源保留在JSON。
8. 运行支持完整响应重放，以及复用成功章节后仅重试失败章节。

## 运行演进

- v1-v4采用“模型逐字复制Claim、Evidence ID和引文”的严格合同，连续暴露
  JSON、ID复制、引文复制和传输稳定性问题。该方案已撤回。
- v5采用实用合同，九章模型响应全部保留，但旧验证器只接受1章。
- v6-v8离线重放v5，加入保守规范化并将人工报告从5118行压缩到557行。
- v9新增`review_synthesis`，但定义过宽，模型将大量跨论文事实综合免于审计；
  同时1章因非法枚举失败。
- v10收紧类型定义：只有当前语料/本综述边界和纯规范性建议可归为
  `review_synthesis`；1章因类型与重要性组合错误失败。
- v11复用v10的8章成功结果，只重试失败的第1章，最终9章完成。
- v12离线复用v11，生成自包含响应目录、Token汇总和最终风险报告。

## 对当前正文的判断

11条核心不支持中，多数不是可以直接断言为“内容错误”，而是结论章提出了
跨论文归纳、路径关系或研究缺口，却没有在对应段落配置可审计引用。模型理由
往往认为推论与知识包相符，但程序仍按“无段内引用、无可展开来源”判为
unsupported。这是发布门禁的预期行为。

当前正文适合作为A/B/C实验中的方案B草稿，不应作为可直接发布的最终综述。
后续若保留该正文，应为这些核心综合结论补引、缩小表述范围或改写为明确的
“本次语料显示”，然后重新审计。

## 验收项

- [x] 章节拆分为事实、综述内部综合和导航Claim。
- [x] 事实Claim标记supported、qualified或unsupported。
- [x] 独立检查result type和validation level写大。
- [x] Evidence不足时按Card、Markdown层级展开冻结来源。
- [x] 核心事实unsupported时整篇不可发布。
- [x] 单章失败保留其他章节，并可只重试失败章节。
- [x] 人工报告无需按Evidence ID跨目录查找。
