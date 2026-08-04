# 全论文池测试报告（2026-08-04）

## 结论

本次已对 `shipping4/papers` 下全部 836 个来源文件完成终态对账。

- 823 篇论文成功生成 Card，并全部完成 DeepSeek 单篇主题筛选。
- 823 篇共生成 39,597 张 Card。
- 原文行到 Region、Region 到 Block、Block 到 Card 三层覆盖率异常均为 0。
- 7 篇论文在 PDF 转换或论文身份校验阶段保持失败终态。
- 3 篇论文因历史上存在多个成功 Card 运行而保持身份歧义终态。
- 1 个 CAJ 文件属于当前不支持格式。
- 2 个文件是内容重复来源，不重复处理。

因此，论文池不是“836 篇全部成功”，而是“836 个来源全部获得可审计终态；其中 823 篇进入 Card 和主题筛选”。

## 最终统计

### 来源与 Card

| 指标 | 数量 |
|---|---:|
| 来源文件 | 836 |
| 唯一内容 | 834 |
| Card 就绪论文 | 823 |
| Card 总数 | 39,597 |
| Gold 结构质量 | 9 |
| Silver 结构质量 | 814 |
| 三层覆盖率异常 | 0 |
| Card 失败论文 | 7 |
| Card 多运行歧义 | 3 |
| 不支持格式 | 1 |
| 重复来源 | 2 |

### 主题筛选

| 相关性 | 数量 |
|---|---:|
| Core | 317 |
| Supporting | 299 |
| Peripheral | 200 |
| Exclude | 7 |
| 合计 | 823 |

最终筛选运行没有失败论文，也没有待筛选论文。

## 未进入筛选的终态

### PDF 转换连续失败

以下 2 篇经过初次运行及两次显式重试后仍为 `convert.pdf_api_failed`：

1. 三峡水库蓄水后长江中游航道整治参数确定方法研究
2. 重庆水运应对三峡通道瓶颈促进高质量发展的对策建议1

### 论文身份无法确定

以下 5 篇 MinerU Markdown 中的论文题名严重缺字、乱码或仅保留外文题名，无法对中文文件身份做确定性校验：

1. 三峡船闸运行管理创新与实践
2. 基于航运大数据的长江干线班轮运营优化研究
3. 永久船闸工程监理协调工作
4. 长江上游航道尺度提升潜力模拟及评价研究
5. 长江航运_强力推进船型标准化

这些论文没有通过猜测题名或复用旧 Card 进入下游。

### 历史 Card 多运行歧义

1. 三峡坝区船舶积压对策探讨
2. 船舶常态化积压情况下的通航组织对策探讨
3. 船舶积压常态化下的三峡枢纽挖潜扩能措施研究

### 不支持格式

- 三峡明渠汛期通航技术研究（CAJ）

### 重复来源

1. 三峡-葛洲坝两坝间航道汛期大流量下船舶航行安全影响因素分析_1
2. 对三峡通航建筑物总体布置及冲沙措施的建议_1

## 完整性审计

823 篇成功论文的三层完整性检查均通过：

1. 每个选定范围内的非空原文行均且仅归入一个 Region。
2. 每个纳入材料的原文行均进入 Block。
3. 每个 Block 均且仅生成一张 Card。

最终仍有 23 篇触发“Card 不超过 2 张或作用域比例低于 0.5”的人工关注条件。复查后：

- 22 篇原 Markdown 不超过 96 行，主要是短讯、简报或同页多文章中的严格选区。
- 唯一超过 100 行的是《长江三峡库区船舶航路改革与实践》，目标范围为 1 至 64 行，生成 11 张 Card；后半部分属于同一 PDF 中的其他文章。
- 不再存在千行学位论文只生成 1 至 2 张 Card 的假成功。

## 本轮修复

真实全池测试推动了以下通用修正：

- 合并空中文题名与相邻正文题名段。
- 合并双语题名及 OCR 异常外文题名段。
- 支持连续多个外文 H1 题名碎片。
- 支持 `[摘要]`、`【摘要】`、`[关键词]`、`【关键词】`。
- 支持学位论文封面 `论文题目` 字段身份校验。
- 识别位于 H2 或普通封面文本中的学位论文标记。
- 正确处理正文之后仍有 H1 的学位论文。
- 防止前置“致谢”把整篇学位论文标为后置材料。
- 支持双栏 OCR 导致的题名片段与正文顺序错乱，但要求组合题名近乎精确匹配并具有足够身份增益。
- 筛选续跑遇到 Card generation 变化时拒绝复用对应论文并自动重筛，而不是中止整个论文池。

## 主要问题码

成功论文中的高频警告如下：

| 问题码 | 论文数 |
|---|---:|
| `parse.unclassified_front_matter` | 790 |
| `parse.bilingual_title_scope_merged` | 160 |
| `parse.inferred_headings` | 100 |
| `parse.document_title_non_h1_match` | 29 |
| `parse.no_structured_body` | 21 |
| `parse.document_title_fuzzy_match` | 15 |
| `parse.fragmented_title_scope_merged` | 10 |
| `parse.no_h1` | 8 |
| `parse.empty_title_companion_scope_merged` | 6 |
| `parse.degree_cover_title_match` | 1 |

这些问题码保持可见，没有通过静默清洗或旧材料复用隐藏。

## 运行代际

- 低交互执行方法：`docs/all_paper_test_low_interaction_execution.md`
- 最终 Inventory：`paper-pool-836files-20260803-v15`
- 剩余 513 篇 Card 长运行：`paper-pool-all-20260803-card-remaining-v1`
- Card 重试：`paper-pool-all-20260803-card-retry20-v1`
- Card 最后重试：`paper-pool-all-20260803-card-retry8-v2`
- DeepSeek 全量筛选：`paper-pool-all-20260803-screen-remaining-v1`
- DeepSeek Schema 重试：`paper-pool-all-20260803-screen-retry2-v1`
- 最终 Card 代际重筛：`paper-pool-all-20260804-screen-final-reconcile-v3`

## 验证

最终代码回归结果：

```text
597 passed, 73 subtests passed
```

最终对账使用当前发布 Card 代际，不读取失败论文的旧材料，也不跨论文合并 DeepSeek 输入。
