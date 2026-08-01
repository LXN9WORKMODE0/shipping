# B2 Phase 3C/3D 风险裁决与定向修订验证记录（2026-08-01）

## 1. 本轮目标

本轮承接 Phase 3B 的逐句 Claim 初审，解决两个问题：

1. 初审模型在安全概括、段落边界和计划 Claim 字面范围上偏保守，不能把单次初审直接当作删除指令；
2. 风险句需要定向处理，但不应反复重写整章，也不能由模型自动覆盖正文。

因此新增两级流程：

```text
Phase 3B 初审风险句
→ Phase 3C 独立裁决（确认 / 驳回 / 重分类）
→ Phase 3D 三类定向修订建议
→ 人工或显式策略选择
→ 创建新 revision
→ 重新执行逐句 Claim 审计
```

## 2. Phase 3C 实现

新增模块：

- `review_claim_audit_b2_adjudication_contracts.py`
- `review_claim_audit_b2_adjudication.py`
- `review_claim_audit_b2_adjudication_report.py`
- `tests/test_review_claim_audit_b2_adjudication.py`

新增 CLI：

```powershell
python main.py llm-review-claim-audit-b2-adjudicate `
  --audit-run-id <audit-run-id> `
  --run-id <adjudication-run-id>
```

核心约束：

- 每次只裁决一个章节的初审风险句；
- 必须重新评估风险句所在段落的全部候选 Claim，顺序和 ID 均不可改变；
- `dismiss` 只能得到 `supported`；
- `confirm` 必须原样保留初审风险集合；
- `reclassify` 必须得到不同的非 `supported` 风险集合；
- `unplanned_claim` 必须给出原句中的精确计划外片段，且该片段不能已存在于候选 Claim；
- 模型不输出发布状态、重要性和修订动作，这些均由程序计算；
- 输入、请求、原始响应、解析响应、验证结果、失败账本和正式输出全部按不可变 run 保存；
- loader 会从上游审计运行确定性重放输入、Schema 和正式输出。

## 3. Phase 3C 真实结果

最终有效裁决运行：

| 章节 | audit run | adjudication run | 初审风险句 | 最终风险句 | 阻断句 |
|---|---|---|---:|---:|---:|
| 1 | `audit-b2-14papers-section-1-20260801-v2` | `adjudication-b2-14papers-section-1-20260801-v1` | 3 | 2 | 2 |
| 2 | `audit-b2-14papers-section-2-20260801-v2` | `adjudication-b2-14papers-section-2-20260801-v1` | 2 | 1 | 1 |
| 4 | `audit-b2-14papers-section-4-20260801-v8` | `adjudication-b2-14papers-section-4-20260801-v2` | 2 | 2 | 2 |
| 5 | `audit-b2-14papers-section-5-20260801-v3` | `adjudication-b2-14papers-section-5-20260801-v2` | 1 | 1 | 0 |
| 6 | `audit-b2-14papers-section-6-20260801-v2` | `adjudication-b2-14papers-section-6-20260801-v1` | 2 | 2 | 1 |
| 7 | `audit-b2-14papers-section-7-20260801-v4` | `adjudication-b2-14papers-section-7-20260801-v2` | 2 | 0 | 0 |
| 8 | `audit-b2-14papers-section-8-20260801-v2` | `adjudication-b2-14papers-section-8-20260801-v1` | 2 | 2 | 1 |

第 3 节初审没有风险句，因此没有调用裁决 API。

总计：

- 初审风险句：14；
- 裁决后驳回：4；
- 裁决后保留：10；
- 其中最终阻断：7；
- 最终七次裁决调用 token：prompt 63,177，completion 6,577，total 69,754。

已暴露并保留的失败：

- 第 4 节 `adjudication-b2-14papers-section-4-20260801-v1`：模型选择 `reclassify`，但输出风险集合未变化；程序以 `adjudication_b2.reclassify_unchanged` 拒绝发布；
- 第 5 节和第 7 节早期运行使用旧的 `uncovered_texts` 约束，只保留为历史运行，不作为当前结论；
- 所有重试均使用新 run ID，没有覆盖失败结果。

## 4. Phase 3C 审视结论

独立裁决是必要的，但不能消除全部边界问题：

1. 初审确有假阳性。第 7 节两句均由计划 Claim 或绑定 Source Window 支持，复核后被驳回；
2. `unplanned_claim` 不是事实错误的同义词。第 2、8 节存在“来源支持、但不属于当前段落计划 Claim”的句子；
3. 当前审计以“段落候选 Claim”为直接白名单，因此同章其他段落已批准的 Claim 在错误段落重复出现时仍会被判为计划外；第 8 节的 2013 年统计句属于此类情况；
4. 对综述质量而言，这类重复句即使事实正确也没有保留必要，删除比放宽审计边界更直接；
5. 允许绑定 Source Window 内的安全低层推论，但不允许据少量文献形成领域趋势、共识或普遍事实。

## 5. Phase 3D 实现

新增模块：

- `review_claim_revision_b2_contracts.py`
- `review_claim_revision_b2.py`
- `review_claim_revision_b2_report.py`
- `tests/test_review_claim_revision_b2.py`

新增 CLI：

```powershell
python main.py llm-review-claim-revision-b2 `
  --adjudication-run-id <adjudication-run-id> `
  --run-id <revision-suggestion-run-id>
```

每个最终风险句固定生成：

1. `minimal_edit`：尽量保留原句结构，只处理风险片段；
2. `conservative_rewrite`：只复述计划 Claim 和来源直接支持的内容；
3. `delete_or_split`：删除无必要句，或拆成两句保留可支持内容。

程序约束：

- 修订输入包含原句、完整所在段、最终风险、裁决理由、候选 Claim、引用和 Source Window；
- 非删除方案必须绑定本段已有 Claim，并只能使用这些 Claim 自带的 citation key；
- Claim ID 和 citation key 是替换句绑定关系的去重并集；
- 不允许原样返回风险句，不允许三个方案给出相同替换文本；
- 删除方案不得携带替换句、Claim 或引用；
- 正式输出固定为 `decision_status=pending`、`auto_applied=false`；
- 报告直接展示原句、所在段落、三种差异、绑定 Claim、引用和来源窗口；
- loader 会从裁决运行确定性重放输入、Schema 和输出。

## 6. Phase 3D 真实结果

最终有效建议运行：

- `revision-b2-14papers-section-1-20260801-v1`
- `revision-b2-14papers-section-2-20260801-v1`
- `revision-b2-14papers-section-4-20260801-v1`
- `revision-b2-14papers-section-5-20260801-v3`
- `revision-b2-14papers-section-6-20260801-v1`
- `revision-b2-14papers-section-8-20260801-v2`

共处理 10 个风险句，生成 30 个候选；六次最终有效调用 token：prompt 53,073，completion 5,946，total 59,019。

已暴露并保留的失败：

- 第 5 节 v1：拆句方案重复 citation key；
- 第 5 节 v2：拆句方案重复 Claim ID；
- 第 8 节 v1：拆句方案重复 citation key；
- 程序没有自动去重；增强提示后以新 run ID 重试。

## 7. 对 30 个候选的语义审视

结构化 Schema 只能证明输出形态和绑定 ID 合法，不能证明候选句本身已经通过语义审计。实际检查发现：

- 第 1 节的部分改写仍保留“重要节点”“值得关注”等未批准概括；
- 第 2 节的候选仍保留饱和运行与滞留常态化事实，但该段的计划 Claim 没有批准这些具体陈述；
- 第 4 节的部分最小修改错误缩窄或混淆了比较对象；
- 第 6 节“提升研究始于”仍包含无法证明的时间起点判断；
- 第 8 节一个候选把“多数数据属于计划外信息”写进正文，明显不适合作为综述文本。

因此不能按方案类型统一自动选择，也不能仅凭 Claim ID 绑定自动应用。

## 8. 推荐的最小决策集

结合原段落上下文，推荐 8 个删除、2 个替换：

| 章节 | 原句摘要 | 推荐动作 | 推荐结果或理由 |
|---|---|---|---|
| 1 | 核心节点、长期饱和 | 删除 | 下一句已有 2013 年统计，删除后段落仍成立 |
| 1 | 运量攀升、积压常态化 | 删除 | 后文作者观点已解释扩能必要性，无需重复未批准概括 |
| 2 | 1.07 亿吨、饱和与滞留 | 删除 | 下一句已有在锚时间和待闸统计，删除后证据链不断 |
| 4 | 不同方式提升幅度明显不同 | 删除 | 后两句已直接比较并排成组、排档算法和综合挖潜研究 |
| 4 | 直接对比困难、缺统一框架 | 保守改写 | “并排成组提升11.7%日闸次的单项量化结果与综合挖潜措施中未拆分调度贡献的总体效果构成对照。” |
| 5 | 工程数据后推导新通道必要性 | 最小修改 | 删除“但这些增量改善……”尾句，只保留来源直接报告的工程数据 |
| 6 | 智能提升始于辅助决策系统 | 删除 | 下一句已完整介绍系统，不影响段落信息量 |
| 6 | 平台整合监测、评估与优化 | 删除 | 下一句已完整介绍平台架构和功能，避免重复与泛化 |
| 8 | 基础统计是量化前提 | 删除 | 后文数据可直接起段，概括无必要 |
| 8 | 2013 年统计与滞留 | 删除 | 同一事实已在本节第 2 段以已批准 Claim 完整出现 |

这组建议刻意偏向删去重复开场和重复数据，而不是把所有有来源但越出段落计划的内容强行改写回去。预计正文信息损失很小，同时能降低重复、泛化和人工复核成本。

## 9. 当前边界与下一步

当前已完成“初审风险 → 独立裁决 → 可审核建议”，但没有修改任何正式章节，也没有将修订建议标记为已选。

下一步需要一个显式选择：

- 接受第 8 节所列的整组推荐；或
- 指出需要保留/改用其他候选的个别句子。

选择后再实现并执行：

1. 决策文件与 decision ID；
2. 程序应用变更，保留原文并生成逐句 diff；
3. 每章创建新的 writing revision ID；
4. 对修订稿重新执行相同的逐句 Claim 审计；
5. 仅当所有章节通过当前门禁后，进入章节装配和独立结论章合同。
