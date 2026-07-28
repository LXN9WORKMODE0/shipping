# 文献语料渐进式披露工作流审计报告

- 运行时间: 2026-07-02T16:58:55+08:00
- run_id: `20260702-165855`
- 机器结果: `workspace\_audit\20260702-165855\results.json`

## 语料概览
- 总 PDF 数: 835
- 大小分布: l(5MB-15MB): 38；m(1MB-5MB): 258；s(200KB-1MB): 416；xl(>15MB): 6；xs(<200KB): 117
- 类型猜测分布: journal_like: 404；news_like: 1；thesis_like: 4；unknown: 426
- 重复文件组数: 5
- 重复文件数: 10

### 重复文件概览
- `三峡-葛洲坝两坝间航道汛期大流量下船舶航行安全影响因素分析`: 三峡-葛洲坝两坝间航道汛期大流量下船舶航行安全影响因素分析.pdf, 三峡-葛洲坝两坝间航道汛期大流量下船舶航行安全影响因素分析_1.pdf
- `三峡危险品船舶待闸锚地的多元化`: 三峡危险品船舶待闸锚地的多元化.pdf, 三峡危险品船舶待闸锚地的多元化_1.pdf
- `三峡工程截流和蓄水碍断航问题研讨`: 三峡工程截流和蓄水碍断航问题研讨.pdf, 三峡工程截流和蓄水碍断航问题研讨_1.pdf
- `三峡水利枢纽货运过闸_翻坝线路优选模型及其应用`: 三峡水利枢纽货运过闸_翻坝线路优选模型及其应用.pdf, 三峡水利枢纽货运过闸_翻坝线路优选模型及其应用_1.pdf
- `对三峡通航建筑物总体布置及冲沙措施的建议`: 对三峡通航建筑物总体布置及冲沙措施的建议.pdf, 对三峡通航建筑物总体布置及冲沙措施的建议_1.pdf

## 环境结论
- `python main.py check` 退出码: 0
- MinerU health: OK
- LLM health: OK
- 是否进入样本真跑: 是

## 分阶段执行
- preflight: 3 篇；停止原因: 无
- pilot: 2 篇；停止原因: 无
- main: 0 篇；状态: skipped

## 样本统计
- 已执行样本数: 5
- summarize 成功数: 5
- 总成功率: 1.0
- 阶段失败率统计: none: 5
- 平均耗时: convert_seconds: 67.312；parse_seconds: 0.027；summarize_seconds: 101.503；total_seconds: 168.936

## 问题清单

### P3
- 处理耗时偏高 (2): 单篇耗时明显偏高，需要关注超时和吞吐。
- 证据 考虑翻坝和天气的长江班轮运网鲁棒优化模型: run=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\run.json, full_md=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\raw\mineru\full.md, structure=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\structure.json, diagnostics=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\diagnostics.json, outline=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\outline.json, summary_tree=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\summary_tree.json, overview=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\overview.json, review_notes=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\review_notes.json
- 证据 长江上游地区产业布局及航运适应性研究: run=workspace\长江上游地区产业布局及航运适应性研究\run.json, full_md=workspace\长江上游地区产业布局及航运适应性研究\raw\mineru\full.md, structure=workspace\长江上游地区产业布局及航运适应性研究\structure\structure.json, diagnostics=workspace\长江上游地区产业布局及航运适应性研究\structure\diagnostics.json, outline=workspace\长江上游地区产业布局及航运适应性研究\structure\outline.json, summary_tree=workspace\长江上游地区产业布局及航运适应性研究\summaries\summary_tree.json, overview=workspace\长江上游地区产业布局及航运适应性研究\summaries\overview.json, review_notes=workspace\长江上游地区产业布局及航运适应性研究\summaries\review_notes.json

## 高频模式
- 语料中存在明显重复文件，后续需要单独去重策略 (5)

## 人工复核队列
- 基于Arena的三峡船舶积压疏导策略效果研究 (journal_like, s(200KB-1MB)): run=workspace\基于Arena的三峡船舶积压疏导策略效果研究\run.json, structure=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\structure.json, diagnostics=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\diagnostics.json, outline=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\outline.json, summary_tree=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\summary_tree.json, overview=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\overview.json, review_notes=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\review_notes.json
- 三峡坝区船舶积压对策探讨 (unknown, m(1MB-5MB)): run=workspace\三峡坝区船舶积压对策探讨\run.json, structure=workspace\三峡坝区船舶积压对策探讨\structure\structure.json, diagnostics=workspace\三峡坝区船舶积压对策探讨\structure\diagnostics.json, outline=workspace\三峡坝区船舶积压对策探讨\structure\outline.json, summary_tree=workspace\三峡坝区船舶积压对策探讨\summaries\summary_tree.json, overview=workspace\三峡坝区船舶积压对策探讨\summaries\overview.json, review_notes=workspace\三峡坝区船舶积压对策探讨\summaries\review_notes.json
- 慎防信息不对称影响船闸管理 (unknown, xs(<200KB)): run=workspace\慎防信息不对称影响船闸管理\run.json, structure=workspace\慎防信息不对称影响船闸管理\structure\structure.json, diagnostics=workspace\慎防信息不对称影响船闸管理\structure\diagnostics.json, outline=workspace\慎防信息不对称影响船闸管理\structure\outline.json, summary_tree=workspace\慎防信息不对称影响船闸管理\summaries\summary_tree.json, overview=workspace\慎防信息不对称影响船闸管理\summaries\overview.json, review_notes=workspace\慎防信息不对称影响船闸管理\summaries\review_notes.json
- 考虑翻坝和天气的长江班轮运网鲁棒优化模型 (thesis_like, xl(>15MB)): run=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\run.json, structure=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\structure.json, diagnostics=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\diagnostics.json, outline=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\outline.json, summary_tree=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\summary_tree.json, overview=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\overview.json, review_notes=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\review_notes.json
- 长江上游地区产业布局及航运适应性研究 (journal_like, l(5MB-15MB)): run=workspace\长江上游地区产业布局及航运适应性研究\run.json, structure=workspace\长江上游地区产业布局及航运适应性研究\structure\structure.json, diagnostics=workspace\长江上游地区产业布局及航运适应性研究\structure\diagnostics.json, outline=workspace\长江上游地区产业布局及航运适应性研究\structure\outline.json, summary_tree=workspace\长江上游地区产业布局及航运适应性研究\summaries\summary_tree.json, overview=workspace\长江上游地区产业布局及航运适应性研究\summaries\overview.json, review_notes=workspace\长江上游地区产业布局及航运适应性研究\summaries\review_notes.json

## 下一步建议
- 当前样本未暴露系统性阻塞问题，可以扩大 pilot 或进入更大样本。

## 人工复核补充
- 审计后人工检查发现 `基于Arena的三峡船舶积压疏导策略效果研究` 的原始机器结果存在隐藏结构问题：正文候选标题已识别，但章节级输出只剩 `结 1 论a` 一个节点。
- 已修复混合“无编号一级标题 + 小节编号 + 晚出现主编号”场景的章节选择规则，并修复 `参 考 文 献<sup>．</sup>` 这类 MinerU HTML 上标噪声导致参考文献未识别的问题。
- 已修复重复解析时 `sections/` 目录残留旧 section 文件的问题。
- 修复后对本次 5 个样本全部本地重跑 parse/summarize；outline、sections、summary roots 分别为 `5/5/5`、`5/5/5`、`5/5/5`、`3/3/3`、`6/6/6`，状态均为 `summarized`。
