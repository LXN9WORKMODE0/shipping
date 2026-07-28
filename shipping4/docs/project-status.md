# 项目状态

更新日期：2026-04-16

这份文档说明三件事：项目现在做到哪一步，当前稳定基线是什么，接下来最值得做的工作是什么。

## 1. 项目要解决的问题

这个项目要把中文期刊论文和中文学位论文从 PDF 转成一套可复用的结构化产物，方便后续做综述、摘录和渐进式披露。核心目标有四个：

- 把 PDF 转成可处理的规范 Markdown。
- 把 Markdown 解析成稳定的 `structure.json`。
- 基于 `structure.json + content_ref` 生成摘要树和综述辅助产物。
- 让后续流程尽量消费结构树，而不是每次重新猜论文层级。

## 2. 目前做到哪里了

现在已经不只是“能跑通”的原型了。工程底座、结构解析主链、低置信修正链和摘要派生链都已经接上，可以把它看成一套可继续迭代的解析系统。

### 已完成的阶段

`Phase 1`：仓库整理完成

- 输入目录固定为 `papers/`。
- 统一工作区固定为 `workspace/<paper_id>/`。
- 代码分层已经拆成 `core / clients / workspace / parsing / pipeline / cli`。
- `tests/`、`docs/` 和忽略规则已经补齐。

`Phase 2`：deterministic 结构判定增强完成

- 标题解析改成“先归一化，再判 family”。
- 前置页判定改成 `pre-body pending + body locked`。
- 正文层级解析改成 body-only hierarchy resolver。
- `diagnostics.json` 增加了 `document_mode`、`numbering_profile`、`area_trace`、`body_start_rejections`、`first_body_*` 等解释字段。

`Phase 3`：低置信节点的 LLM 修正链完成

- LLM 只处理低置信节点。
- 合并时限制可修改字段，避免破坏结构树。
- 失败时可以 fallback，不会阻断 parse 主流程。
- 修正记录会落到 `refinement.json`。

`Phase 4`：摘要派生链完成

- 已有 `summary_tree.json`、`overview.json`、`review_notes.json`。
- 摘要阶段只依赖 `structure.json + content_ref`，不再从散落章节文件里反推结构。

### 当前判断

现在的项目状态可以概括成一句话：主链已经齐了，接下来重点不是继续堆功能，而是用真实语料把结构解析打磨到更稳。

## 3. 当前稳定基线

现在有几个约束已经比较明确，不建议轻易再改：

- `structure.json` 是唯一的结构真相源。
- `diagnostics.json` 负责解释判定过程、边界和低置信来源。
- `summary_tree.json`、`overview.json`、`review_notes.json` 都是派生产物。
- `sections/` 只是导出视图，不是结构判断的依据。
- LLM 不负责整篇论文的完整层级，只负责修低置信节点。

如果后面继续迭代，最好都沿着这个基线走，不要再回到“多份产物各自维护一套结构判断”的旧路。

## 4. 当前工作流

### 输入

- 用户把 PDF 放进 `papers/`。

### 转换

- `convert` 调用 MinerU。
- 原始结果写到 `workspace/<paper_id>/raw/mineru/`。
- 规范 Markdown 写到 `workspace/<paper_id>/normalized/document.md`。

### 解析

- `parse` 读取规范 Markdown。
- 生成：
  - `workspace/<paper_id>/structure/structure.json`
  - `workspace/<paper_id>/structure/diagnostics.json`
  - `workspace/<paper_id>/structure/outline.json`
  - `workspace/<paper_id>/sections/*.md`

### 修正

- 如果存在低置信节点，可以触发 LLM refinement。
- 修正记录写到 `workspace/<paper_id>/structure/refinement.json`。

### 摘要

- `summarize` 基于结构树和 `content_ref` 生成：
  - `workspace/<paper_id>/summaries/summary_tree.json`
  - `workspace/<paper_id>/summaries/overview.json`
  - `workspace/<paper_id>/summaries/review_notes.json`

## 5. 测试和质量状态

当前仓库测试基线已经通过：

- `pytest -q`
- 最近一次结果：`61 passed`

已经覆盖的重点包括：

- 标题归一化和 family 判定。
- 学位论文与期刊论文的结构解析。
- `0 引言` 和中文全角混合编号的回归问题。
- plain body heading 的回归问题。
- diagnostics、outline、refinement、summary 的派生产物。
- `convert -> parse -> summarize` 的 stub 集成流程。

从工程角度看，当前仓库已经具备继续做真实语料审计和定向修复的条件。

## 6. 下一步更值得做什么

现在最合理的下一步不是继续加新功能，而是基于最新的 phase2 基线，重新跑一轮真实语料审计。

建议顺序：

1. 用当前代码重新执行分层抽样审计。
2. 重点看真实论文里还剩哪些 `P1 / P2` 结构问题。
3. 先修高频误判模式，再扩大样本量。
4. 等结构树在真实语料上更稳定之后，再继续优化摘要质量。

### 眼下最值得盯的几个点

- 学位论文前置页的变体是否还有漏判。
- 目录后的“图目录 / 表目录 / 符号说明”是否稳定。
- 混合编号样式在真实论文里是否还会触发 profile fallback。
- plain body heading 会不会被过度打成低置信。
- 长文档在 `summarize` 阶段的耗时和质量是否明显退化。

## 7. 建议阅读顺序

- [README.md](/C:/Users/DELL/Desktop/Python/CURSOR/shipping4/README.md)
- [architecture.md](/C:/Users/DELL/Desktop/Python/CURSOR/shipping4/docs/architecture.md)
- [workspace-layout.md](/C:/Users/DELL/Desktop/Python/CURSOR/shipping4/docs/workspace-layout.md)
- [execution-roadmap.md](/C:/Users/DELL/Desktop/Python/CURSOR/shipping4/docs/execution-roadmap.md)
- [CHANGELOG.md](/C:/Users/DELL/Desktop/Python/CURSOR/shipping4/docs/CHANGELOG.md)
