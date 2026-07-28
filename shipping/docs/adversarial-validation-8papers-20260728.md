# 8 篇高风险格式论文验证记录

## 目标

本轮优先验证 PDF 到 Card 的稳健性，不把普通格式重复通过当作新增结论。只有通过三层完整性屏障的论文才进入单篇主题分析。

综述主题：三峡枢纽通航能力、调度优化与碍断航风险。

冻结清单：`config/pilots/adversarial-validation-8papers-20260728.json`。

## 样本选择

| 论文 | PDF 大小 | 主要风险假设 |
|---|---:|---|
| 三峡永久船闸水力学问题研究 | 80,496 B | 小体积老文献、水力学公式、扫描/OCR |
| 三峡船闸运行评价模型研究 | 141,528 B | 评价指标、模型公式、表格 |
| 三峡水利枢纽货运过闸_翻坝线路优选模型及其应用 | 162,074 B | 文件名与标题符号差异、优化模型、表格 |
| 三峡二期施工碍（断）航问题应引起重视 | 95,503 B | 标题特殊括号、短篇老文献、OCR |
| 三峡枢纽碍航度测算及对策建议 | 132,273 B | 计算指标、数据表、短篇结构 |
| 基于元胞自动机的水运枢纽运输组织研究 | 18,443,301 B | 长学位论文、多级章节、公式与仿真 |
| 极端场景下三峡-葛洲坝枢纽通航调度策略仿真研究 | 10,393,407 B | 长文档、连字符标题、复杂调度仿真 |
| 三峡船舶过闸调度规则优化及其仿真验证 | 3,980,379 B | 中型论文、优化规则、仿真图表 |

以上风险是根据文件大小和题名预判，尚未当作已确认事实。实际问题以 MinerU 产物、解析问题码和三层覆盖账本为准。

## 运行记录

### PDF 到 Card 最终结果

| 论文 | Block | Card | 内容类型 | 结构问题 | 三层覆盖 |
|---|---:|---:|---|---|---|
| 三峡永久船闸水力学问题研究 | 5 | 1 | prose | `document_title_non_h1_match`、`no_structured_body` | 1.0 / 1.0 / 1.0 |
| 三峡船闸运行评价模型研究 | 51 | 35 | abstract、image、prose、table | `unclassified_front_matter` | 1.0 / 1.0 / 1.0 |
| 三峡水利枢纽货运过闸_翻坝线路优选模型及其应用 | 46 | 19 | abstract、image、prose、table | `unclassified_front_matter` | 1.0 / 1.0 / 1.0 |
| 三峡二期施工碍（断）航问题应引起重视 | 7 | 1 | prose | `no_structured_body` | 1.0 / 1.0 / 1.0 |
| 三峡枢纽碍航度测算及对策建议 | 50 | 24 | image、prose、table | `unclassified_front_matter` | 1.0 / 1.0 / 1.0 |
| 基于元胞自动机的水运枢纽运输组织研究 | 700 | 295 | abstract、image、prose、table | `unclassified_front_matter` | 1.0 / 1.0 / 1.0 |
| 极端场景下三峡-葛洲坝枢纽通航调度策略仿真研究 | 566 | 350 | abstract、image、prose、table | `unclassified_front_matter` | 1.0 / 1.0 / 1.0 |
| 三峡船舶过闸调度规则优化及其仿真验证 | 442 | 232 | abstract、image、prose、table | `inferred_headings`、`unclassified_front_matter` | 1.0 / 1.0 / 1.0 |

合计 1867 个 Block、957 张 Card。8 篇均有当前成功 generation，并通过显式清单验收。

### 暴露并处理的问题

1. `三峡永久船闸水力学问题研究.pdf` 是包含多篇文章的合订 PDF。MinerU 把目标论文标题识别为 H2，后续无关论文标题识别为 H1。旧范围选择只看 H1，因此首轮错误选中“Au 在低温 PVF 衬底上的分形生长”，身份屏障以 `parse.document_identity_mismatch` 拒绝发布。
2. 范围选择器现只在 H1 选择失败时搜索全部 Markdown 标题，并继续要求标题相似度、唯一性和最小分差满足原合同。非 H1 目标范围截止于下一个同级或更高层级标题，重复同分标题仍拒绝。实际重跑选中 L1-L12，Card 最大来源行为 L11，后续文章未进入材料。
3. `基于元胞自动机的水运枢纽运输组织研究` 首轮在下载 MinerU 结果包时发生 `IncompleteRead(20518162 bytes read, 737703 more expected)`，未产生 Markdown。没有发布材料，也没有使用旧代际。为区分偶发传输中断，只进行一次新的完整运行，第二代完成并生成 295 张 Card。
4. 原真实语料验收脚本硬编码 17 篇，并扫描工作区中的旧试验目录，扩大语料后产生假失败。验收脚本现支持 `--collection`，只检查清单中的当前论文、当前 generation 和合并语料中的对应材料，不再用固定篇数代表完整性。
5. 两篇短文没有可恢复的章节层级，程序以 `parse.no_structured_body` 明示并各生成一张整篇 Card。这是可见的低粒度结果，不是静默丢失。

验收命令：

```powershell
..\.venv\Scripts\python.exe scripts/validate_md_card_corpus.py `
  --workspace workspace `
  --collection config/pilots/adversarial-validation-8papers-20260728.json
```

结果：8 个论文目录通过；所有当前非 red 文档三层覆盖完整。

### 单篇分析状态

用户已明确授权将这 8 篇论文的 Card 内容发送给外部 DeepSeek 服务，并按“一篇论文一个隔离任务”执行。

| 论文 | 最终 run | 来源 Card | 入选 Card | 有效 Evidence | 最终状态 |
|---|---|---:|---:|---:|---|
| 三峡永久船闸水力学问题研究 | `adversarial-8papers-20260728--brief-001` | 1 | 1 | 1 | `completed` |
| 三峡船闸运行评价模型研究 | `adversarial-8papers-20260728--brief-002-r3` | 35 | 4 | 3 | `completed_with_failures` |
| 三峡水利枢纽货运过闸_翻坝线路优选模型及其应用 | `adversarial-8papers-20260728--brief-003-r3` | 19 | 4 | 1 | `completed_with_failures` |
| 三峡二期施工碍（断）航问题应引起重视 | `adversarial-8papers-20260728--brief-004` | 1 | 1 | 1 | `completed` |
| 三峡枢纽碍航度测算及对策建议 | `adversarial-8papers-20260728--brief-005-r2` | 24 | 6 | 3 | `completed_with_failures` |
| 基于元胞自动机的水运枢纽运输组织研究 | `adversarial-8papers-20260728--brief-006-r3` | 295 | 6 | 6 | `completed_with_revisit_failure` |
| 极端场景下三峡-葛洲坝枢纽通航调度策略仿真研究 | `adversarial-8papers-20260728--brief-007` | 350 | 8 | 8 | `completed` |
| 三峡船舶过闸调度规则优化及其仿真验证 | `adversarial-8papers-20260728--brief-008` | 232 | 8 | 8 | `completed` |

8 篇均形成可供下游读取的单篇简报，共产生 31 条有效 Evidence。其中 3 篇存在部分入选 Card 合同失败，1 篇基础简报有效但自动回查未闭合。这些状态没有改写为完全成功。

### 单篇分析暴露并处理的问题

1. OCR 会把小数写成 `0 . 2 5`。数值支持校验现仅在数学或 TeX 上下文识别这类空格小数，不对普通文本做宽泛归一化。
2. 源文 `1×108t` 不能被模型解释成 `1×10⁸t`。合同继续拒绝这种没有明确指数标记的改写，并在提示中明确禁止。
3. 模型曾把相邻 Card 的行范围拼成一个不存在的 `material_id`。模型侧输入现使用运行内短别名，合同验证后再由程序映射回真实 ID，模型不再直接生成长 ID。
4. 单张入选 Card 的合同失败不再中断整篇论文。仅初始 Evidence 抽取阶段允许隔离 `ContractViolation` 和主题合同错误；网络错误、程序错误或全部 Card 失败仍使整篇运行失败。
5. 部分失败写入 `audit/evidence_failures.jsonl`，人工报告直接展示失败 Card 的标题、原文位置、完整内容和错误，不要求根据 Evidence 编号反查。

### 跨论文综合

冻结清单：`config/pilots/adversarial-synthesis-8papers-20260728.json`。

前两次运行分别暴露了两类模型合同问题：

1. `adversarial-synthesis-8papers-20260728`：同一 Evidence 同时进入已分配和未分配集合，合同以 `evidence_map.assigned_and_unassigned` 拒绝。
2. `adversarial-synthesis-8papers-20260728-r2`：一个综合单元重复引用同一主题 ID，合同以 `schema.topic_review_outline_invalid` 拒绝。

程序没有自动猜测或修补模型 JSON。提示合同补充集合互斥和单元内 ID 唯一约束后，`adversarial-synthesis-8papers-20260728-r3` 完成：

- 输入：8 篇论文、31 条 Evidence。
- 主题矩阵：7 个主题。
- 多主题归属：2 条 Evidence 同时归入 2 个主题；观测最大值等于配置上限 2。
- 未归属 Evidence：1 条，显式列入审计。
- 提纲：11 个综合单元、7 个章节。
- 进入提纲：22 条 Evidence；另有 8 条已分配但未进入提纲，ID 明示在覆盖审计中。
- 跨论文主题：4 个；跨论文综合单元：4 个。
- 语料缺口：8 个，其中 3 个进入优先缺口。
- API：2 次请求，共计 19,726 tokens，其中输入 14,291、输出 5,435。

最终人工报告：
`workspace/_topic_syntheses/runs/adversarial-synthesis-8papers-20260728-r3/review/topic_synthesis.md`。

## 本轮结论

1. PDF 到 Card 在这 8 篇高风险样本上通过三层覆盖屏障，但短文仍可能只有整篇级 Card，Card 粒度不等于解析质量完全一致。
2. 单篇分析可以在保留严格合同的前提下隔离少数坏 Card；部分状态和失败内容必须对下游及人工报告可见。
3. 跨论文综合已验证“一条 Evidence 最多对应两个主题”的设计，实际出现了 2 条双主题 Evidence，没有出现全场覆盖。
4. 当前主要风险已从静默遗漏转为可见的 OCR 数值损坏、表格语义支持不足和模型合同失败；这些问题已进入失败账本，而不是被兜底改写。
