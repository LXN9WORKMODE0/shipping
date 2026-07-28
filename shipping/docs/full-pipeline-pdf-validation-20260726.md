# 真实 PDF 全链路验证记录（2026-07-26）

## 1. 验证目标

使用两篇真实 PDF 验证以下单次调度链路：

```text
PDF
→ MinerU 精确解析
→ Markdown
→ Card
→ 单篇主题简报
→ 自动回查
→ 跨论文主题映射
→ 综述提纲
→ 顶层正式结果
```

测试文献：

- 《船舶常态化积压情况下的通航组织对策探讨》
- 《三峡坝区船舶积压对策探讨》

验证主题为“三峡船舶积压疏导策略与长江航运组织”。所有运行使用新的论文 ID 和不可变 run ID，不复用既有 Markdown，不在失败后切换解析器。

## 2. PDF 到 Card

两篇 PDF 在四轮运行中均成功通过 MinerU：

- `mode=precise`
- `model_version=vlm`
- `language=ch`
- `state=done`
- 表格和公式识别开启

第四轮结果：

| 论文 | 结构质量 | Card | 原文行到 region | 纳入行到 block | block 到 Card |
| --- | --- | ---: | ---: | ---: | ---: |
| 船舶常态化积压情况下的通航组织对策探讨 | silver | 2 | 1.0 | 1.0 | 1.0 |
| 三峡坝区船舶积压对策探讨 | silver | 20 | 1.0 | 1.0 | 1.0 |

第一篇仅生成 2 张 Card。MinerU Markdown 只有 16 行且没有可用正文标题，程序按未结构化正文处理；逐行分类账和后续两层覆盖均闭合，因此这是上游解析粒度较低，不是 MD-to-Card 静默丢失。

## 3. 四轮顶层运行

### 第一轮

run ID：`full-pipeline-pdf-2papers-20260726`

- PDF 到 Card：两篇完成。
- 两篇 Evidence 均完成。
- 两篇基础简报均因 JSON 合同失败。
- 问题分别为合理的 `point_type=definition` 未入枚举，以及单个 `evidence_unit_ids` 被模型写成字符串。
- 单篇简报屏障生效，未调用跨论文综合。

处理：

- 正式增加 `definition` 类型。
- Prompt 明确单个 Evidence ID 也必须放在 JSON 数组中。
- 没有对失败响应做字符串包装或其他自动纠正。

### 第二轮

run ID：`full-pipeline-pdf-2papers-v2-20260726`

- 两篇基础简报均可用。
- 第二篇自动回查因 `point_type=result` 未入枚举而局部失败，基础 6 条 Evidence 继续有效。
- 跨论文综合进入提纲阶段，但一个综合单元引用了其声明主题之外的 Evidence。
- `outline.unit_evidence_outside_themes` 硬校验阻断正式发布。

处理：

- 增加 `result` 类型。
- 综合 Prompt 明确 Evidence 必须来自单元声明主题的 assignments。
- 使用同一批 7 条 Evidence 独立重跑 Flash 综合，严格校验通过：7 条 Evidence 全部入题、入纲，7 个综合单元全部入章。
- 该独立综合没有发现真实跨论文综合关系，明确记录 `cross_paper_synthesis_absent`，没有伪造一致性。

### 第三轮

run ID：`full-pipeline-pdf-2papers-v3-20260726`

- 第一篇基础简报完成。
- 第二篇使用 Evidence 已经支持的年份写入 caution，但旧合同禁止所有导航字段出现数字。
- 单篇简报屏障生效，未调用综合。

处理：

- 将“一律禁止导航数字”改为逐条数字溯源。
- 引用 Evidence 中已有数字可以通过；未出现在所引 Evidence 中的数字以 `brief.numeric_fact_unsupported` 失败。
- 没有删除或改写模型数字。

### 第四轮

run ID：`full-pipeline-pdf-2papers-v4-20260726`

- 两篇 PDF 到 Card 完成。
- 两篇基础简报均完成；共保留 7 条基础 Evidence。
- 第二篇回查产生未被所引 Evidence 支持的 `2010`，局部回查被正确拒绝，基础简报保留。
- Flash 在主题映射中把 4 条 Evidence 重复归入多个主题，触发 `evidence_map.duplicate_assignment`。
- 顶层没有发布 `pipeline_result.json`。

同输入 Pro 对照：

- Pro 仍把 1 条 Evidence 重复归入两个主题。
- 同样触发 `evidence_map.duplicate_assignment`。
- Pro 比 Flash 重复更少，但仍未通过当前单主题归属合同。

## 4. 代码修正

- 简报观点类型增加 `definition` 和 `result`，报告增加中文标签。
- 简报 Prompt 明确 `evidence_unit_ids` 必须始终为数组。
- 导航数字由字符禁令改为 Evidence 数值溯源校验。
- 综合 Prompt 明确综合单元 Evidence 与声明主题的归属关系。
- 增加“定义”“结果”“有证据数字通过、无证据数字拒绝”反例测试。

## 5. 定性结论

当前可以确认：

- 真实 PDF → MinerU 精确 Markdown → Card 已通过本次验证。
- Card → 基础单篇简报已在第四轮同时通过两篇真实论文。
- 自动回查的局部失败隔离有效，不会污染基础简报。
- 跨论文综合代码可在独立运行中完成并证明 Evidence 全覆盖。
- 顶层阶段屏障和失败不发布机制均按设计工作。

当前不能确认：

- 真实 PDF 到顶层正式结果的单次运行已经稳定通过。

唯一明确阻塞已收敛为主题归属合同：当前程序要求每条 Evidence 只能有一个主主题，而 Flash 和 Pro 都可能把同一 Evidence 放入多个相关主题。下一步需要在以下两种正式语义中选择：

1. 保持单主主题合同，为主题映射增加一次有记录、受限的校正调用。
2. 正式允许一条 Evidence 进入最多两个主题，并在覆盖审计中显式记录多主题复用。

建议选择第一种。它保留唯一主归属和可解释覆盖统计，同时把初次失败、校正请求和校正结果全部写入账本，不做静默去重。

## 6. 本地回归验证

- 主题简报、跨论文综合和顶层调度针对性测试：50 项通过。
- 全量单元测试：257 项通过。
- `compileall`：通过。
- `git diff --check`：通过。

## 7. 有限多主题归属试验

经讨论，单主题归属被改为显式配置：

```json
{
  "schema_version": "llm.topic_synthesis_config.v2",
  "max_themes_per_evidence": 2
}
```

新合同要求：

- 每条 Evidence 必须进入至少一个主题，或者明确未归组。
- 每条 Evidence 最多进入两个不同主题。
- 同一主题内不得重复同一 Evidence。
- 超过上限直接以 `evidence_map.assignment_limit_exceeded` 失败，不截断。
- 覆盖率继续按唯一 Evidence ID 计算，另行记录主题归属总数和多主题 Evidence。

使用第四轮同一批 7 条 Evidence 和 Flash 重跑：

- 主题：4 个。
- 唯一 Evidence：7 条。
- 主题归属记录：8 条。
- 仅归属一个主题：6 条。
- 归属两个主题：1 条。
- 单条 Evidence 最大主题数：2。
- 主题映射阶段通过，没有触发归属上限。

本次运行随后在提纲阶段以 `outline.unit_paper_count_invalid` 失败：一个引用两篇独立论文的综合单元被模型标为 `single_source_context`。该问题与 Evidence 多主题归属无关，不能作为把上限扩展到 3 的依据。当前继续保持上限 2。

## 8. 第五至第八轮暴露的问题

第五至第八轮继续使用新的论文 ID 和 Pro 模型验证，失败均保留原始响应，没有修改失败结果后继续发布。

### 第五轮

run ID：`full-pipeline-pdf-2papers-v5-pro-20260726`

- Evidence 中的 `4,280.9` 被数字校验器错误拆分，触发 `citation.evidence_claim_numeric_fact_unsupported`。
- 修正千分位小数识别，增加 `4,280.9` 反例测试。

### 第六轮

run ID：`full-pipeline-pdf-2papers-v6-pro-20260726`

- 多级表头表格被旧解析器当成单级表头，触发 `citation.table_numeric_scope_unprovable`。
- 表格范围改为记录连续多级表头，数值观点必须引用全部表头和对应数据行。
- 增加两级表头构造与完整引用测试。

### 第七轮

run ID：`full-pipeline-pdf-2papers-v7-pro-20260726`

- 基础 Evidence 已完成，但简报的研究缺口问题含有 Evidence 已支持的数字，旧合同仍一律禁止数字。
- 研究缺口改为与其他简报字段相同的 Evidence 数字溯源：已验证数字可用，未验证数字直接失败。
- 使用同一篇第二论文独立重跑后完成，共 8 条 Evidence、12 次请求、40761 tokens。

### 第八轮

run ID：`full-pipeline-pdf-2papers-v8-pro-20260726`

- 模型对两级表头表格只选择顶层年份表头和数据行，遗漏第二层“南线/北线/合计”表头。
- 严格表格门槛正确拒绝该 Evidence，没有放宽校验。
- Evidence Prompt 现在显式提供确定性解析器生成的 `required_header_quote_ids` 和 `eligible_data_row_quote_ids`；使用数据行时，多级表头必须全部引用。
- 增加提示构造测试，证明两级表头的两个 quote ID 均进入真实模型输入结构。

隔离复测 run ID：`pdf-verify-v8-table-scope-hint-pro-v2-20260726`

- 基础单篇分析完成：20 张 Card 选择 6 张，生成 6 条 Evidence。
- 自动回查因新增简报数字未获引用 Evidence 支持而局部失败，基础简报继续可用。
- 本次随机主题筛选没有再次选择两级表格 Card，因此只能确认提示已进入代码和测试结构，不能把这次隔离复测表述为模型已实际生成两级表头 Evidence。

## 9. 第九轮单次全链路通过

run ID：`full-pipeline-pdf-2papers-v9-pro-20260726`

顶层状态为 `completed`，正式发布了 `output/pipeline_result.json`。

### PDF 到 Card

两篇论文均满足：

- MinerU `mode=precise`、`model_version=vlm`、`language=ch`。
- `enable_table=true`、`enable_formula=true`、服务端 `state=done`。
- 结构质量为 `silver`。
- `原文行→region=1.0`、`纳入行→block=1.0`、`block→Card=1.0`。
- 未归属原文行 0，未制卡 block 0。

第一篇生成 2 张 Card，第二篇生成 20 张 Card。

### 单篇分析

- 第一篇：状态 `completed`，1 条 Evidence，自动回查无候选。
- 第二篇：状态 `completed`，8 条 Evidence，自动回查完成。
- 两篇均为 `core`，没有单篇失败码。

### 跨论文综合

- 来源 Evidence 9 条，主题归属记录 12 条。
- 6 条单主题，3 条双主题，单条 Evidence 最大主题数为 2。
- 未归组 Evidence 0，未进入提纲 Evidence 0。
- 综合单元 7 个，全部进入提纲。
- 研究缺口 3 个，全部进入优先级列表。
- 形成 1 个跨论文主题和 1 个跨论文综合单元。
- 综合质量标记为空。

### 成本

- 单篇分析与跨论文综合共 18 次 LLM 请求。
- Prompt 48185 tokens，Completion 9748 tokens，总计 57933 tokens。

### 当前结论

可以确认，当前代码已经在两篇真实 PDF 上完成一次不复用旧产物的：

```text
PDF → MinerU 精确 Markdown → Card → 单篇主题简报
→ 自动回查 → 跨论文主题映射 → 综述提纲 → 顶层正式结果
```

这证明链路已经跑通，但不是大规模稳定性证明。两篇语料中第一篇的 MinerU 正文粒度仍较低；第九轮也没有再次选中第八轮的两级表格 Card。后续扩大语料时应继续统计阶段失败率，尤其关注表格 Evidence 合同和 LLM 严格 JSON 合同，而不是把单次通过解释为所有格式均已解决。
