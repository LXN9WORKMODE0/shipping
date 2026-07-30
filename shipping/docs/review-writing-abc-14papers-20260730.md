# 14篇论文综述写作A/B/C实验

## 实验问题

验证“Understanding + Landscape + Framework + 分章丰富上下文写作”是否比以下
两种方案更适合作为默认综述写作链：

- A：同一14篇完整Markdown与题录，一次直接写作。
- B：Paper Understanding、Research Landscape、Review Framework、章节知识包、
  分章写作和写后Claim审计。
- C：旧Topic Review Evidence、Topic Synthesis和Evidence-only写作。

最终评价运行：

`review-evaluation-abc-14papers-20260730-v4`

报告：

`workspace/_review_evaluations/runs/review-evaluation-abc-14papers-20260730-v4/review/evaluation_report.md`

## 自动指标

| 指标 | A 直接全文 | B 新链路 | C Evidence-only |
|---|---:|---:|---:|
| 正文字符 | 4,629 | 20,001 | 3,943 |
| 论文覆盖率 | 100.00% | 100.00% | 92.86% |
| 核心维度覆盖率 | 100.00% | 100.00% | 100.00% |
| 跨论文比较段落比例 | 23.53% | 87.72% | 33.33% |
| 事实Claim支持率 | 97.06% | 85.22% | 54.10% |
| 核心事实支持率 | 95.83% | 85.45% | 97.06% |
| 核心不支持 | 0 | 11 | 0 |
| result type写大 | 0 | 14 | 0 |
| validation level写大 | 1 | 14 | 1 |
| 引用论文匹配率 | 96.46% | 79.94% | 74.07% |
| 语料缺口越界 | 0 | 0 | 0 |
| 结构评分 | 3/5 | 5/5 | 2/5 |
| 比较评分 | 2/5 | 5/5 | 1/5 |

## Token

| 方案 | 请求数 | 输入Token | 输出Token | 合计Token |
|---|---:|---:|---:|---:|
| A | 2 | 685,743 | 23,817 | 709,560 |
| B | 34 | 4,512,503 | 121,441 | 4,633,944 |
| C | 124 | 1,242,890 | 74,464 | 1,317,354 |

统计范围：

- A：直接写作 + 方案A Claim审计。
- B：14篇Understanding + Landscape + Framework + 9章写作 + 9章审计。
- C：14篇Topic Review全阶段 + Topic Synthesis + Evidence-only写作 +
  方案C Claim审计。
- 三稿共用的结构评价Token单独保存，不归入任一方案。
- 未配置供应商计价表，不推算费用。

## 定性结论

### A：直接全文写作

A自然生成的是一篇约4,600字的紧凑综述。它覆盖全部14篇和全部核心维度，
事实支持率最高，引用匹配最好，只有1条validation level写大。缺点是跨论文
比较较少，结构只是可用而非深入。

这说明用户此前“直接把全文交给大模型可能更好”的判断在本次样本上有事实依据：
A的第一版较短，但更稳、更便宜，也更接近可以继续人工扩写的起点。

### B：丰富上下文分章写作

B生成约2万字，结构和跨论文比较显著优于A、C。它确实解决了Evidence-only
写作内容单薄、逐项罗列的问题。

但丰富上下文也诱发了更多跨论文归纳、关系推断和结论扩展。230条事实Claim中
只有196条直接支持，存在21条qualified、13条unsupported、11条核心
unsupported，并有14条result type和14条validation level写大。结论章尤其
存在“推理本身可能合理，但段落没有可审计引用”的问题。

因此B当前是内容丰富的研究草稿，不是可直接发布的综述。

### C：Evidence-only写作

C篇幅最短、只覆盖13篇，结构和比较评分最低。虽然其核心事实支持率数值较高，
但它只有34条核心事实，全部61条事实的总体支持率仅54.10%，另有20条
qualified和8条unsupported。因此不能据“核心支持率”单项认为C优于A或B。

## 架构门槛

既定门槛：

1. B核心事实支持率不低于A：未通过，85.45% < 95.83%。
2. B跨论文比较和结构评分高于C：通过，5/5 > 1/2。
3. B人工修改时间低于C至少25%：未执行；自动硬门槛已经失败，无法改变结论。

最终状态：

`fail_automatic_gate`

## 架构决定

不进入Phase 5，不把B链路接入默认Pipeline或UI。

当前项目应收缩为以下稳定能力：

1. PDF/Markdown/Card独立处理和论文池管理。
2. Paper Understanding、Landscape和Framework作为可选研究分析与材料组织层。
3. 完整Markdown直接写作作为轻量草稿基线。
4. Claim审计、引用定位和风险报告作为写作后的质量控制层。

不继续把“更长、更复杂的自动写作链”作为默认目标。后续若再次尝试B式写作，
应作为单独实验，先解决审计驱动的引用修订和结论降格，不直接推进UI集成。

## 实现与审计说明

- A、B、C均转换为统一的段落和论文citation key。
- A、C分别使用同一14篇冻结知识包执行一次全文Claim审计。
- B复用`claim-audit-14papers-20260730-v12`。
- 事实支持与结构评分分开，结构模型不能覆盖Claim审计结论。
- 结构评价中模型错误列入的“明确未越界”项目由程序删除，并记录
  `non_overreach_entry_removed`。
- 人工修改时间和修改字符比例保持`pending`，未伪造人工数据。
