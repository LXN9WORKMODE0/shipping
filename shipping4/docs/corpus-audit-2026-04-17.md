# 文献语料渐进式披露工作流审计报告

- 运行时间: 2026-04-17T10:21:01+08:00
- run_id: `20260417-102101`
- 机器结果: `workspace\_audit\20260417-102101\results.json`

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
- main: 0 篇；状态: pending

## 样本统计
- 已执行样本数: 5
- summarize 成功数: 4
- 总成功率: 0.8
- 阶段失败率统计: none: 5
- 平均耗时: convert_seconds: 61.815；parse_seconds: 52.209；summarize_seconds: 439.226；summary_independence_seconds: 756.09；total_seconds: 1286.142

## 问题清单

### P3
- 处理耗时偏高 (4): 单篇耗时明显偏高，需要关注超时和吞吐。
- 证据 基于Arena的三峡船舶积压疏导策略效果研究: run=workspace\基于Arena的三峡船舶积压疏导策略效果研究\run.json, full_md=workspace\基于Arena的三峡船舶积压疏导策略效果研究\raw\mineru\full.md, structure=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\structure.json, diagnostics=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\diagnostics.json, outline=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\outline.json, summary_tree=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\summary_tree.json, overview=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\overview.json, review_notes=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\review_notes.json
- 证据 三峡坝区船舶积压对策探讨: run=workspace\三峡坝区船舶积压对策探讨\run.json, full_md=workspace\三峡坝区船舶积压对策探讨\raw\mineru\full.md, structure=workspace\三峡坝区船舶积压对策探讨\structure\structure.json, diagnostics=workspace\三峡坝区船舶积压对策探讨\structure\diagnostics.json, outline=workspace\三峡坝区船舶积压对策探讨\structure\outline.json, summary_tree=workspace\三峡坝区船舶积压对策探讨\summaries\summary_tree.json, overview=workspace\三峡坝区船舶积压对策探讨\summaries\overview.json, review_notes=workspace\三峡坝区船舶积压对策探讨\summaries\review_notes.json
- 证据 慎防信息不对称影响船闸管理: run=workspace\慎防信息不对称影响船闸管理\run.json, full_md=workspace\慎防信息不对称影响船闸管理\raw\mineru\full.md, structure=workspace\慎防信息不对称影响船闸管理\structure\structure.json, diagnostics=workspace\慎防信息不对称影响船闸管理\structure\diagnostics.json, outline=workspace\慎防信息不对称影响船闸管理\structure\outline.json, summary_tree=workspace\慎防信息不对称影响船闸管理\summaries\summary_tree.json, overview=workspace\慎防信息不对称影响船闸管理\summaries\overview.json, review_notes=workspace\慎防信息不对称影响船闸管理\summaries\review_notes.json

## 高频模式
- 语料中存在明显重复文件，后续需要单独去重策略 (5)

## 人工复核队列
- 基于Arena的三峡船舶积压疏导策略效果研究 (journal_like, s(200KB-1MB)): run=workspace\基于Arena的三峡船舶积压疏导策略效果研究\run.json, structure=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\structure.json, diagnostics=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\diagnostics.json, outline=workspace\基于Arena的三峡船舶积压疏导策略效果研究\structure\outline.json, summary_tree=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\summary_tree.json, overview=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\overview.json, review_notes=workspace\基于Arena的三峡船舶积压疏导策略效果研究\summaries\review_notes.json
- 三峡坝区船舶积压对策探讨 (unknown, m(1MB-5MB)): run=workspace\三峡坝区船舶积压对策探讨\run.json, structure=workspace\三峡坝区船舶积压对策探讨\structure\structure.json, diagnostics=workspace\三峡坝区船舶积压对策探讨\structure\diagnostics.json, outline=workspace\三峡坝区船舶积压对策探讨\structure\outline.json, summary_tree=workspace\三峡坝区船舶积压对策探讨\summaries\summary_tree.json, overview=workspace\三峡坝区船舶积压对策探讨\summaries\overview.json, review_notes=workspace\三峡坝区船舶积压对策探讨\summaries\review_notes.json
- 慎防信息不对称影响船闸管理 (unknown, xs(<200KB)): run=workspace\慎防信息不对称影响船闸管理\run.json, structure=workspace\慎防信息不对称影响船闸管理\structure\structure.json, diagnostics=workspace\慎防信息不对称影响船闸管理\structure\diagnostics.json, outline=workspace\慎防信息不对称影响船闸管理\structure\outline.json, summary_tree=workspace\慎防信息不对称影响船闸管理\summaries\summary_tree.json, overview=workspace\慎防信息不对称影响船闸管理\summaries\overview.json, review_notes=workspace\慎防信息不对称影响船闸管理\summaries\review_notes.json
- 考虑翻坝和天气的长江班轮运网鲁棒优化模型 (thesis_like, xl(>15MB)): run=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\run.json, structure=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\structure.json, diagnostics=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\diagnostics.json, outline=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\structure\outline.json, summary_tree=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\summary_tree.json, overview=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\overview.json, review_notes=workspace\考虑翻坝和天气的长江班轮运网鲁棒优化模型\summaries\review_notes.json

## 下一步建议
- 当前样本未暴露系统性阻塞问题，可以扩大 pilot 或进入更大样本。
