# 项目讨论与当前状态总结

更新日期：2026-07-03

## 1. 背景问题

最初的困惑来自论文解析和渐进式披露之间的关系：如果论文格式差异很大，似乎很难把所有论文都拆成可靠层级；如果结构层不稳定，后续渐进式披露、综述材料整理和综述生成就缺少基础。

讨论后的判断是：不应该把目标设成“万能论文解析器”，也不应该直接改成“高级模型/skills 黑盒读论文写综述”。前者会被无穷格式兼容拖住，后者会把证据、状态、错误和复跑能力埋进模型上下文。

## 2. 当前总体目标

当前目标是开发一条通用、可追溯的综述材料生产线：

```text
论文源文件 -> 规范文本 -> 结构质量判定 -> 综述材料单元 -> 跨论文语料 -> 面向综述任务的材料包
```

核心原则：

- 解析层不承诺完美拆解所有论文。
- 解析成功时使用章级材料。
- 解析不稳时显式降级为证据块。
- 不做静默兜底；有问题就记录 issue，让问题暴露出来。
- 高级模型后续只消费已经显化的材料包，而不是直接吞整批论文。
- 新方案隔离在 `shipping/` 目录下，不和旧的 `shipping4/` 主链混在一起。

## 3. 设计取舍

讨论中过了三种路线：

1. 继续强化章级解析。
   - 优点：和旧的 `structure.json -> summary_tree` 路线一致。
   - 问题：会持续被论文格式、OCR 噪声、目录页和标题断裂拖住。

2. 放弃结构解析，直接让高级模型抽取。
   - 优点：短期看起来快。
   - 问题：可追溯性、批量复跑、错误定位都弱。

3. 章级解析为主，质量闸门兜住失败，失败时产出证据块。
   - 这是当前采用的路线。
   - 目标不是让每篇论文都变成完美树，而是让每篇论文都能产出有质量标记、可追溯的材料或明确问题。

## 4. 当前实现

当前新方案位于：

```text
shipping/
```

主要代码：

- `shipping/main.py`
- `shipping/src/shipping_pipeline/pipeline.py`
- `shipping/src/shipping_pipeline/audit.py`
- `shipping/src/shipping_pipeline/models.py`
- `shipping/tests/test_pipeline.py`

当前五层 pipeline：

1. `convert`
   - 支持 `.md`、`.markdown`、`.txt`。
   - 暂不接 PDF/MinerU。
   - PDF 等不支持输入会记录 `convert.unsupported_input` 并失败。

2. `parse`
   - 识别章级标题，例如：
     - `第一章 绪论`
     - `第 2 章 方法`
     - `一、前言`
   - 输出：
     - `structure/structure.json`
     - `structure/quality.json`
   - 质量标签：
     - `gold`：章级结构可用。
     - `silver`：结构不可靠，但文本可切成证据块。
     - `red`：文本不足或输入无法处理。

3. `material`
   - `gold` 输出 `chapter` 材料。
   - `silver` 输出 `evidence_chunk` 材料。
   - 材料保留 `paper_id`、`content_ref`、`confidence_flags` 等追踪字段。

4. `corpus`
   - 输出跨论文语料：
     - `workspace/_corpus/papers.jsonl`
     - `workspace/_corpus/materials.jsonl`
     - `workspace/_corpus/issues.jsonl`

5. `review_pack`
   - 输出：
     - `workspace/_review_packs/<topic-slug>.json`
   - 当前是轻量证据矩阵，不接 LLM。

## 5. 当前真实 Markdown 审计结果

已对旧系统已有 MinerU Markdown 产物做了一轮真实审计：

输入：

```text
shipping4/workspace/*/normalized/document.md
```

命令：

```powershell
python shipping/main.py audit shipping4/workspace --workspace shipping/workspace --topic "研究 方法 结论" --run-id real-md-20260703
```

输出：

- `shipping/workspace/_audit/real-md-20260703/results.json`
- `shipping/workspace/_audit/real-md-20260703/report.md`

结果摘要：

```text
total: 5
gold: 4
silver: 1
issues:
- parse.no_chapter_roots: 1
```

逐篇结果：

- `三峡坝区船舶积压对策探讨`
  - `gold`
  - 5 个 `chapter` 材料

- `基于Arena的三峡船舶积压疏导策略效果研究`
  - `silver`
  - 8 个 `evidence_chunk` 材料
  - issue: `parse.no_chapter_roots`

- `慎防信息不对称影响船闸管理`
  - `gold`
  - 5 个 `chapter` 材料

- `考虑翻坝和天气的长江班轮运网鲁棒优化模型`
  - `gold`
  - 7 个 `chapter` 材料
  - 旧方案里这类带空格的 `第 2 章` 章标题曾造成漏章，新方案当前可以识别。

- `长江上游地区产业布局及航运适应性研究`
  - `gold`
  - 6 个 `chapter` 材料

## 6. 当前未做的事

当前明确没有做：

- 没有接 PDF 转换。
- 没有调用 MinerU。
- 没有调用 LLM。
- 没有生成综述正文。
- 没有把旧 `shipping4/` 改造成新方案。
- 没有尝试对所有论文格式做规则兜底。

这些暂时不做，是为了先验证中间层是否站得住：已有 Markdown 能否稳定变成可追溯的材料和问题清单。

## 7. 当前判断

这轮结果说明新方向是可行的：

- 章级解析能覆盖一部分真实 Markdown。
- 不适合章级解析的论文可以显式进入 `silver`，转成证据块。
- 问题不会被假装成成功结构，而是进入 `issues.jsonl` 和 audit report。

但当前样本只有 5 篇，不能推断 835 篇全量语料的表现。下一步应该扩大样本，而不是马上接 PDF 或 LLM。

## 8. 建议下一步

建议下一步继续做真实语料审计增强：

1. 扩大 batch audit 样本量。
2. 统计 `gold/silver/red` 分布。
3. 统计高频 issue。
4. 对 `silver` 样本人工看材料质量。
5. 再决定是否要：
   - 补解析规则；
   - 调整 quality gate；
   - 引入 LLM 做标题清洗和材料抽取；
   - 接回 PDF/MinerU 转换层。

当前最重要的问题不是“怎么写综述”，而是“材料层是否足够可靠、问题是否足够透明”。
