# B2 Phase 3B：写后语义 Claim 审计验证报告

日期：2026-08-01

分支：`codex/b2-review-pipeline`

验证范围：B2 Phase 3A 的 8 个非结论章节、14 篇真实论文、DeepSeek V4 Pro 非思考模式

## 1. 定性结论

B2 Phase 3B 已建立可用的逐句语义审计基础，但当前结果只能作为风险初筛，不能直接作为
自动删改依据。

已证明：

- 审计单位保持为单章，不跨章混合上下文；
- 每章只读取最终正文、实际实现的 Claim 和这些 Claim 绑定的 Source Window；
- 程序稳定切句，并固定句子、段落、Claim 和引用身份；
- 模型逐句评估所在段落的全部计划 Claim，不能创造 Claim ID；
- 风险分类采用 B2 指南中的细分类型；
- `blocking`、修订动作、人工确认候选和 `publishable` 均由程序计算；
- 运行输入、Schema、原始响应、失败、输出和中文报告均不可变且可重放；
- 最终统一代际的 8 章审计均完成。

尚未证明：

- 单次模型审计对边界性综合句足够稳定；
- 14 个风险句全部是真实越界，而不是审计模型的保守判断；
- 自动重写风险句后能够保持原有 Claim 覆盖、章节连贯性和长度；
- 未经二次裁决的风险可以直接进入删除、拆句或降格操作。

因此，下一步不能直接批量改写正文。应先增加只复核风险句的独立裁决层，再仅对确认风险
执行定向修订。

## 2. 本阶段实现

### 2.1 独立 B2 审计代际

新增运行根目录：

```text
workspace/_review_claim_audits_b2/runs/<run_id>/
```

新增合同：

- `llm.review_claim_audit_b2_config.v1`
- `llm.review_claim_audit_b2_input.v1`
- `llm.review_claim_audit_b2_draft.v1`
- `llm.review_claim_audit_b2.v1`
- `llm.review_claim_audit_b2_run.v1`

新增 CLI：

```text
llm-review-claim-audit-b2
```

B1 审计代码和历史运行保持不变，B2 loader 不接受 B1 产物。

### 2.2 句子目录

程序从 B2 章节生成稳定句子目录，每句保存：

- `sentence_id`；
- 段落 ID、段落序号和句内序号；
- 原句文本；
- 所在段落全部 `implemented_claim_ids`；
- 所在段落引用集合。

真实验证发现，按分号拆句会把“前者/后者”比较结构拆开，造成假阳性。最终只按句号、
问号和叹号切分，分号保留在同一个审计句中。

### 2.3 Claim 覆盖方向

每个句子必须逐项评估所在段落的全部计划 Claim：

```text
supports_all_content
supports_part_of_content
contradicts_content
not_relevant
```

评估方向固定为“Claim 及其来源能否覆盖当前句内容”，不是“当前句是否完整复述整个 Claim”。
较宽 Claim 可以完整支持一个较窄句子；一个句子也可以由多条 Claim 共同覆盖。

程序从评估结果反推 `planned_claim_ids`。章节声明实现的每个 Claim 必须至少映射到一个句子，
否则整次审计失败。

### 2.4 风险分类与发布门禁

风险类型：

```text
supported
qualified
missing_citation_binding
unsupported_inference
overgeneralized_synthesis
result_type_overstatement
validation_level_overstatement
source_mismatch
contradicted
unsupported_fact
unplanned_claim
```

动态 JSON Schema 强制：

- `supported` 必须单独出现；
- 非 `supported` 数组只能使用风险类型；
- 句子顺序和全部 Claim 评估顺序固定；
- 输出不能包含程序派生字段。

程序派生：

- `sentence_role`；
- `planned_claim_ids`；
- `importance`；
- `recommended_actions`；
- `blocking`；
- `requires_revision`；
- `human_confirmation_required`；
- `publishable`。

只要存在未解决风险，当前审计就不允许发布。`blocking` 另外区分指南定义的强制阻断项。

### 2.5 人工可读报告

报告不要求人工查找机器 ID。每条风险句直接展示：

- 原句；
- 风险、重要性、阻断状态、理由和程序建议动作；
- 实际映射 Claim 的完整计划文本和强度；
- 相关 Source Window 的原文、类型和 citation key。

支持句保留原句和映射 Claim，但不重复展开来源窗口，控制报告长度。

## 3. 最终八章初筛结果

最终统一代际：

| 节 | 审计 run ID | 句子 | supported | 待复核 | 阻断 | 人工候选 | 初筛可发布 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | `audit-b2-14papers-section-1-20260801-v2` | 7 | 4 | 3 | 3 | 0 | 否 |
| 2 | `audit-b2-14papers-section-2-20260801-v2` | 9 | 7 | 2 | 2 | 1 | 否 |
| 3 | `audit-b2-14papers-section-3-20260801-v3` | 11 | 11 | 0 | 0 | 0 | 是 |
| 4 | `audit-b2-14papers-section-4-20260801-v8` | 10 | 8 | 2 | 2 | 0 | 否 |
| 5 | `audit-b2-14papers-section-5-20260801-v3` | 10 | 9 | 1 | 0 | 1 | 否 |
| 6 | `audit-b2-14papers-section-6-20260801-v2` | 9 | 7 | 2 | 2 | 0 | 否 |
| 7 | `audit-b2-14papers-section-7-20260801-v4` | 7 | 5 | 2 | 2 | 2 | 否 |
| 8 | `audit-b2-14papers-section-8-20260801-v2` | 8 | 6 | 2 | 2 | 1 | 否 |

合计：

- 71 个句子；
- 57 个句子初筛为 `supported`；
- 14 个句子需要二次裁决；
- 13 个句子按当前初筛分类属于阻断项；
- 5 个句子属于高风险数字或高风险综合的人工确认候选；
- 第 3 节是唯一初筛直接通过的章节。

风险分类累计：

| 分类 | 次数 |
| --- | ---: |
| `supported` | 57 |
| `unplanned_claim` | 14 |
| `unsupported_inference` | 4 |
| `unsupported_fact` | 3 |
| `qualified` | 2 |
| `overgeneralized_synthesis` | 1 |
| 其他风险 | 0 |

同一句可以有多个风险，因此风险次数不能直接相加为风险句数。

最终八次调用：

- 输入 115,723 Token；
- 输出 29,673 Token；
- 均为非思考模式；
- `finish_reason` 均为 `stop`。

## 4. 调试失败与合同修正

本阶段共保留 26 次真实 API 运行：

- 完成 20 次，包括中间合同下的历史成功运行；
- 失败 6 次；
- 输入 396,895 Token；
- 输出 99,425 Token。

失败类型：

| 错误 | 次数 | 暴露的问题 |
| --- | ---: | --- |
| `supported_assessment_invalid` | 2 | Claim 覆盖方向含糊；多 Claim 共同覆盖规则过强 |
| `navigation_contract_invalid` | 1 | 模型一边标导航，一边映射实质 Claim |
| `risk_action_incompatible` | 1 | 修订动作不应由模型自由选择 |
| `supported_mixed_with_risk` | 1 | supported 互斥关系需要进入动态 Schema |
| `JSONDecodeError` | 1 | `finish_reason=stop` 时供应商仍可能返回损坏 JSON |

对应修正：

1. 修订动作改为程序按风险类型生成。
2. `sentence_role` 改为程序根据 Claim 映射和风险派生。
3. Claim 评估枚举改为明确的“来源覆盖句子内容”方向。
4. 分号不再切句。
5. supported 与其他风险的互斥进入 JSON Schema。
6. 允许多条 Claim 共同覆盖一个句子。
7. 明确安全概括不因缺少具体数字自动降格。
8. 明确具名论文陈述不因缺少“本次语料”限定自动判为过度概括。

所有旧运行均保留，没有覆盖或读取“最近成功运行”兜底。

## 5. 当前核心问题：单次审计不够稳定

同一正文在不同审计合同和独立调用中出现过明显分类变化。例如第 7 节在修正分号切分后曾
得到 7/7 句 supported，最终独立运行又得到 2 个边界风险。第 3 节早期曾有 4 个
unplanned Claim，修正角色与评估方向后为 11/11 句 supported。

其中一部分变化来自已确认的合同错误，但即使合同固定，边界性综合句仍可能因模型保守程度
不同而波动。因此：

- 单次 supported 可以暂时接受；
- 单次风险不能直接触发删除；
- 风险句需要独立、聚焦的二次裁决；
- 二次裁决只读取风险句、初审理由、映射 Claim 和相关窗口，不重审全部支持句；
- 只有裁决确认的风险才进入自动定向修订。

这不是人工逐条审核。人工只在“初审风险、二次裁决和修订后复审”仍无法闭合时介入。

## 6. 测试结果

B2 Phase 3B 定向测试：

```text
13 passed
```

覆盖：

- 稳定句子目录和分号比较句；
- supported 互斥；
- 多 Claim 共同覆盖；
- 程序派生导航角色和重要性；
- 无映射实质句的 unplanned Claim 要求；
- 核心与 supporting 风险的阻断差异；
- 全部 implemented Claim 必须映射；
- 不可变运行、加载器重放和中文来源报告；
- 独立 CLI。

项目全量测试：

```text
505 passed, 73 subtests passed
```

Python 编译检查通过。

## 7. 下一步

进入 Phase 3C：风险句自动裁决与定向修订。

最小顺序：

1. 为每章只提取 `requires_revision=true` 的句子。
2. 建立独立裁决运行，冻结初审风险、原句、Claim 和窗口。
3. 裁决结果仅允许 `confirm`、`dismiss` 或 `reclassify`。
4. 程序重新计算最终风险和门禁，不允许裁决模型直接给 publishable。
5. 对确认风险生成最多一个最小改写，不改动其他句子和段落。
6. 程序保存前后 diff、新 revision ID 和风险绑定。
7. 用修订后的章节重新执行完整 Phase 3B 审计。
8. 只有复审通过的核心 Claim 才能进入结论输入。
