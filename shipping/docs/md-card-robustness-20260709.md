# MD-to-Card 稳健性报告 - 2026-07-09

> 2026-07-10 更正：本文下方 v2 的“正文不静默丢失”结论已撤回。v2 只证明了 block→card 覆盖，没有证明 selected segment→region 覆盖。以“2026-07-10 完整性闭环 v3”为当前结论。

## 范围

本报告记录大范围验证之后对 Markdown-to-card 稳健性的增强。

输入：

- `shipping/workspace` 下已有 normalized Markdown
- 17 篇真实论文

输出：

- 审计 run：`md-card-robust-20260709`
- 材料审计 run：`material-quality-robust-20260709`
- 人工审核 run：`material-review-robust-20260709`

## 代码改动

1. 阿拉伯数字 Markdown 标题现在会被视为章节根节点。
   - 示例：`## 1 研究方法`
   - `## 1.1 数据来源` 这类小数编号子标题不会被提升为章节根。

2. 普通编号行现在会被检测，但只作为推断结构。
   - 示例：`1 引言`
   - 这类运行标记为 `silver`。
   - parse 问题：`parse.inferred_headings`
   - node/card 标记：`heading.inferred`

3. 弱语义标题会被暴露，而不是被隐藏。
   - pipeline 不再把正文前 18 个字符当作可靠标题。
   - 如果无法推断语义标题，材料卡标题为 `未识别主题片段 N`。
   - 质量标记和审计问题：`title.weak_inferred`

4. 过短且畸形的 H1 标题不会用于论文标题推断。
   - 这避免了 `工` 这类单字标题被当作论文标题。

## 前后对比

改动前：

- run：`md-card-wide-20260709`
- 论文数：`17`
- 材料数：`211`
- 质量：`gold=8`，`silver=9`
- parse 问题：
  - `parse.no_chapter_roots=9`
- 材料审计：
  - `summary.repeats_title=3`
  - `title.polluted=1`

改动后：

- run：`md-card-robust-20260709`
- 论文数：`17`
- 材料数：`213`
- 质量：`gold=9`，`silver=8`
- parse 问题：
  - `parse.no_chapter_roots=2`
  - `parse.inferred_headings=4`
  - `parse.chapter_sequence_gap=3`
- 材料审计：
  - `title.weak_inferred=70`

## 解释

解析器比之前更稳健，但没有用静默放宽规则的方式掩盖问题。

旧的 `parse.no_chapter_roots=9` 实际混合了三类情况：

1. Markdown 中有可用的阿拉伯数字标题，但解析器之前没有识别。
2. 文档中有普通编号标题，但没有 Markdown 标题语法。
3. 文档确实缺少可靠章节根节点。

新的结果把这些情况拆开了：

- Markdown 阿拉伯数字标题会正常解析。
- 普通编号标题会被使用，但明确暴露为推断结构。
- 仍有两篇论文没有可靠章节根节点。
- 有三篇论文暴露出章节编号不连续。

`title.weak_inferred` 增加是有意的。之前这些卡片使用正文前缀标题，看起来可读，但不是稳定语义标题；现在它们被显式标为审核风险。

## 通病

1. MinerU Markdown 中常见阿拉伯数字章节标题。
   - 现在已支持 `## 1 ...` 这类一级章节。

2. 短论文中存在普通编号标题。
   - 现在可以解析，但因标题语法是推断出来的，结果为 `silver`。

3. 一些论文存在编号跳跃或混合标题层级。
   - 现在会暴露为 `parse.chapter_sequence_gap`。

4. 材料卡标题仍是当前最弱环节。
   - 70 张卡片需要更好的语义标题。
   - 这现在是显式审计问题，不再伪装成正常标题。

5. 已有 review pack 可能因 card ID 变化而过期。
   - 当前审计仍会对旧包报告 `review_pack.missing_material`。

## 个例

- `三峡蓄水对重庆物流的影响`
  - 改动前：silver，`parse.no_chapter_roots`
  - 改动后：gold
  - 原因：`## 1 三峡蓄水给重庆物流发展带来了机遇` 这类标题现在能识别。

- `三峡水库对坝区河段航运条件的影响及对策`
  - 改动前：silver，`parse.no_chapter_roots`
  - 改动后：gold
  - 原因：`## 0 引 言`、`## 1 ...`、`## 2 ...` 这类标题现在能识别。

- `三峡工程截流和蓄水碍断航问题研讨`
  - 改动后：silver，`parse.inferred_headings`
  - 原因：存在可用结构，但结构来自普通编号行推断。

- `三峡航运遇瓶颈`
  - 改动后：silver，`parse.inferred_headings`
  - 原因：章节根节点是推断出来的，而不是显式 Markdown 标题。

- `基于运输化理论的三峡船闸过闸货运需求量增长趋势分析`
  - 仍为 silver，`parse.no_chapter_roots`
  - 原因：没有找到可靠章节根节点。

- `扩大三峡船闸通航能力的措施研究`
  - 仍为 silver，`parse.no_chapter_roots`
  - 原因：没有找到可靠章节根节点。

- `长江上游地区产业布局及航运适应性研究`
  - 仍为 gold，但存在大量弱标题问题。
  - 原因：长章节会被切成多张卡，一些切分后的卡片无法仅靠本地关键词获得稳定语义标题。

## 当前结论

MD-to-card 在结构层已经更稳健：

- 能识别更多真实标题。
- 减少了误报的 `no_chapter_roots`。
- 能显式暴露推断结构和章节编号跳跃。

下一瓶颈是材料标题质量：

- 解析器已经能产出可追溯材料卡。
- 但许多材料卡在进入人工审核或 LLM 分析前，还需要更好的语义标题。

---

## 2026-07-10 文档地图重构结果

本轮不修改 MinerU 和 LLM 分析，只重构以下链路：

```text
normalized/document.md
  -> 目标文档定界
  -> 摘要/正文/后置区域
  -> 原子内容块
  -> material.v2
  -> 结构与材料质量审计
```

对应产物：

- 批量解析：`md-card-structure-v2b-20260710`
- 材料审计：`material-quality-structure-v2b-20260710`
- 人工审核：`material-review-structure-v2b-20260710`
- 真实验收脚本：`shipping/scripts/validate_md_card_corpus.py`

### 新旧结果

| 指标 | 2026-07-09 旧版 | 2026-07-10 新版 |
|---|---:|---:|
| 论文数 | 17 | 17 |
| 材料卡数 | 213 | 503 |
| 质量分布 | gold=9, silver=8 | silver=17 |
| 文档范围无法确定 | 未单独检查 | 0 |
| 正文覆盖率不足 | 未单独检查 | 0 |
| 后置内容污染 | 未单独检查 | 0 |
| `[table removed]` | 实施计划记录 55 张 | 0 |
| 短尾卡 | 未单独检查 | 1 |
| 完全重复 normalized 文档组 | 未单独检查 | 1 |
| 跨论文重复内容指纹组 | 未单独检查 | 21，涉及 2 对论文 |

新版全部为 silver，不表示正文覆盖退化。新版判定要求同时检查前置内容、文档范围、标题来源和媒体遗漏；17 篇论文的 `body_coverage_ratio` 均为 `1.0`，但每篇至少存在一项已记录的结构或来源质量问题，因此没有标为 gold。

材料类型分布：

- 摘要卡：19
- 正文卡：384
- 表格卡：99
- 图片占位卡：1

真实语料验收结果：0 项失败。验收脚本检查了 17 篇论文、503 张卡的来源范围、块唯一归属、卡片长度、后置区域隔离、表格保留和 6 个已知难例。

### 已解决的问题

1. `三峡航运遇瓶颈` 只使用 L25-L44 的目标文章范围，未混入同一 Markdown 中的其他新闻。
2. Arena 论文 L15、L23、L44、L71、L95 所在结构均有对应材料，且材料不超过 L104。
3. `1、2、3、4、` 编号标题能够形成结构；`2000 年，Yu 和 Li...` 未被提升为标题。
4. 参考文献、作者简介、致谢等后置区域不进入材料卡。
5. 表格保留单元格文本，不再替换成 `[table removed]`。
6. 普通文本标题和 `摘要` 结构标签不再重复进入卡片正文。
7. 数值比较式 `0<K<=0.1` 不再被材料审计误判为 HTML。

### 仍存在的问题

以下问题保持暴露，没有自动修补或删除：

- `card.short_tail`
  - 论文：`考虑翻坝和天气的长江班轮运网鲁棒优化模型`
  - material_id：`考虑翻坝和天气的长江班轮运网鲁棒优化模型:abstract:L00100-L00100:01`
  - source_span：L100-L100
  - 原因：英文摘要末尾形成独立短片段，无法在 1400 字符上限内与前卡合并。
- `media.image_omitted`
  - 论文：`三峡航运遇瓶颈`
  - material_id：`三峡航运遇瓶颈:image:L00037-L00037:01`
  - source_span：L37-L37
  - 原因：当前 MD-to-Card 只保留图片 alt/目标信息，不解析图片内容。
- `title.polluted`
  - 论文：`基于Arena的三峡船舶积压疏导策略效果研究`
  - 受影响 material_id：L73 正文卡、L76 表格卡、L79 表格卡、L81-L93 正文卡
  - 污染标题：`仿真结果与结果分析Record`
  - 原因：MinerU Markdown 标题本身含 OCR 残留 `Record`；本层保留并报告，不猜测改写。
- `corpus.duplicate_source`
  - 论文组：`mineru-smoke-20260709` 与 `慎防信息不对称影响船闸管理`
  - 两个目录的 normalized Markdown 完全相同。
- `corpus.duplicate_content`
  - 上述完全重复论文产生 15 个重复内容指纹组。
  - `三峡工程截流和蓄水碍断航问题研讨` 与同名 `_1` 目录产生 6 个重复内容指纹组。
  - 合计 21 组、42 张带问题标记的材料卡；未自动去重。

结构问题：

- `parse.unclassified_front_matter=15`：15 篇论文在首个正文结构前存在无法进一步分类的内容，来源范围保留在结构文件中，不进入材料卡。
- `parse.no_h1=3`：`三峡工程截流和蓄水碍断航问题研讨`、同名 `_1`、`三峡工程给川江航运带来的机遇及对策`。
- `parse.inferred_headings=2`：两份 `三峡工程截流和蓄水碍断航问题研讨` 使用普通编号行推断标题。
- `parse.no_structured_body=2`：`三峡航运遇瓶颈`、`扩大三峡船闸通航能力的措施研究` 只有未分层正文。
- `parse.document_title_fuzzy_match=1`：Arena 论文 H1 丢失 `Arena`，通过唯一模糊匹配定界，候选分数写入结构文件。
- `review_pack.empty_evidence_matrix=1`、`review_pack.missing_material=1`：旧审核包仍引用重构前的材料 ID；当前新人工审核包不复用旧勾选结果。

### 当前判定

该 v2 判定已撤回。全面复核发现 13 个 selected segment 内的非空行没有归入任何 region，其中至少 5 行是正文或摘要。`body_coverage_ratio=1.0` 当时只表示已生成 block 全部进入 card，不能推出原文完整。

---

## 2026-07-10 完整性闭环 v3

对应产物：

- 批量解析：`md-card-integrity-v3-20260710`
- 材料审计：`material-quality-integrity-v3-20260710`
- 人工审核：`material-review-integrity-v3-20260710`

### 新的不变量

1. selected segment 内每个非空行必须且只能归为 `scope_marker/front_matter/abstract/keywords/body/back_matter` 之一。
2. coverage 分成三层独立证明：`原文行→region`、`纳入行→block`、`block→card`。
3. 任一层存在未归属、重叠或缺失时，运行直接变为 red，不发布部分材料。
4. 每次运行有唯一 `generation_id`；当前材料、最新 run 和 corpus 三者代际必须一致。
5. 失败重跑会归档上一成功代际、撤下稳定材料入口并重建 corpus，旧卡不会继续暴露。

### 真实结果

- 论文目录：17
- 当前可发布论文：16
- red：1
- 当前材料卡：490
- 16 篇可发布论文的三层覆盖均为 `1.0 / 1.0 / 1.0`
- red 目录：`mineru-smoke-20260709`
- red 原因：目录 ID 与唯一 H1 `慎防信息不对称影响船闸管理` 不匹配，问题代码 `parse.document_identity_mismatch`
- red 目录当前材料卡：0；上一代材料已归档，不在 corpus 中

原来遗漏的正文/摘要已经进入材料：

- `川江滚装运输发展的SWOT分析及对策` L9 → `prose:L00009-L00009:01`
- `基于运输化理论的三峡船闸过闸货运需求量增长趋势分析` L9 → `prose:L00009-L00009:01`
- `三峡蓄水对重庆物流的影响` L17、L19、L27 → `abstract:L00015-L00027:01`

另外 8 个原未归属行是期刊元数据或关键词，包括中图分类号、文章编号、DOI 和 `Key wor ds`。它们现在也进入逐行分类账，分别标为 `front_matter` 或 `keywords`，不再处于无归属状态。

### 内容保真修复

- `0<K<=0.1 and K-E>0.1` 原样保留，不再被通用 `<[^>]+>` 删除。
- 缺少 `</table>` 的表格在结构行结束后停止，不吞后续正文，并标记 `table.parse_failed`。
- 普通文本 `参 考 文 献` 可以启动 back matter。
- `Abstr act`、`Key wor ds` 等 OCR 间断标签能够识别。
- `start_char/end_char` 只在可映射到单一原文行时写入真实坐标；人工审核展示全部精确 `source_spans`。
- 中文文件名不再统一退化为 `untitled`；已有 review pack 拼音文件名保持兼容。

### 当前未解决范围

LLM 分析接口仍处于“可调用”阶段，本轮没有修改。以下事项留给下一目标：

- 正式 JSON Schema 和字段类型/枚举校验。
- analysis card ID 唯一性。
- 单卡失败账本与不中断整篇的执行策略。
- 原始响应、失败 material_id、模型版本、prompt 哈希和 token 使用量存档。
- 人工审核决策文件与 LLM 重跑选择机制。

MinerU 已丢失字符、图片内容和 OCR 污染标题也没有在 MD-to-Card 层猜测修复，仍按问题代码暴露。
