# 14篇论文 Research Landscape 验收记录

## 验收对象

- 主题：三峡枢纽通航能力的提升方法
- 语料范围：`targeted_sample`
- 输入：14个显式指定且已通过重放校验的 Paper Understanding
- 接受运行：`landscape-14papers-20260730-v9`
- 正式输出：`workspace/_research_landscapes/runs/landscape-14papers-20260730-v9/output/research_landscape.json`
- 人工报告：`workspace/_research_landscapes/runs/landscape-14papers-20260730-v9/review/research_landscape.md`

## 接受运行结果

- 状态：`completed`
- 论文覆盖：14/14
- 研究维度：7
- 跨论文关系：8
- 年代关注阶段：4
- 明确分歧：0
- 本次语料缺口：4
- 未映射论文：0
- 领域缺口候选：0
- 输入Token：40,842
- 输出Token：4,111
- `finish_reason`：`stop`
- 隐藏推理字符：0

## 失败与修正记录

失败运行全部保留，未覆盖原始响应，也未把部分结果发布为正式图谱。

| 运行 | 状态 | 暴露的问题 | 处理 |
|---|---|---|---|
| `landscape-14papers-20260730` | completed但不接受 | 把论文沉默当成对立，并写出无来源的年代承继 | 增加明确关系和非因果年代合同 |
| `-v2` | failed | 维度列出论文但没有引用该论文贡献 | 保留失败，强化贡献所有权 |
| `-v3` | failed | 两篇已进入维度的论文又被列为未映射；另有伪分歧 | 强化互斥和分歧语义边界 |
| `-v4` | failed | 当前供应商拒绝聚合平台模型名 | 改用已有官方模型配置，不修改语料 |
| `-v5` | failed | 年代条目缺少一篇论文自己的贡献 | 增加显式所有权索引和逐项自检 |
| `-v6` | failed | API连接 `IncompleteRead` | 保留传输失败，使用新运行ID显式重试 |
| `-v7` | failed | 维度仍漏引一篇论文自己的贡献 | 将所有权条件编译进模型生成Schema |
| `-v8` | completed但不接受 | 结构通过，但把解释层次差异和组合方案写成分歧 | 明确“不同层次、单项与组合、范围差异”均不构成分歧 |
| `-v9` | completed并接受 | 机器合同和人工语义审视通过 | 作为Phase 2输入 |

## 技术结论

并列的 `paper_ids` / `contribution_ids` 仅靠提示词不能稳定保证所有权。最终实现保留公开 `llm.research_landscape.v1` 合同，同时生成动态JSON Schema：论文一旦出现在维度、年代条目或分歧立场中，对应贡献数组必须包含该论文名下至少一条贡献；relation的双方也分别受同样约束。公开验证Schema和模型生成Schema分别留档。

`targeted_sample` 下没有生成领域缺口字段。v9 的四项缺口均表述为当前14篇语料未充分回答的问题。人工审视未发现伪分歧、由年份推演的传承关系或论文覆盖遗漏。

## 残余边界

relation仍属于模型综合判断，不是论文原话。个别“互补”“共同构成”等措辞需要在写作阶段结合展开的贡献和Card来源使用，不能直接升级为因果关系或经验事实。该边界已在中文报告中通过来源展开和判断类型隔离。

## 结论

Research Landscape Phase 2通过。它可以作为Review Framework的显式上游；不需要回退到旧Evidence-only主题矩阵，也不需要修改现有默认Pipeline和UI。
