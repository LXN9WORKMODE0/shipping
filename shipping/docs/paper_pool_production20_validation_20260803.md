# 全论文池首批 20 篇生产性扩容验证

日期：2026-08-03

## 结论

本轮验证通过。现有“论文池清点 → MinerU 精确解析 → Card → 单篇主题筛选”可以从小样本进入每批 50 篇的持续处理阶段。

这不表示每篇 Markdown 都完美，而是说明：

1. 20 篇 PDF 全部完成制卡，单篇失败不会阻断批次的机制未触发异常。
2. 成功结果具备逐篇 generation、来源身份、检查点和三层覆盖证明。
3. 内容审计发现的 1 个正文截断问题能够被 Card 数量和选段比例识别，修复后真实重跑有效。
4. 20 篇最终全部完成 DeepSeek 单篇筛选，没有论文滞留在“已有 Card 但未筛选”状态。
5. 1M 上下文不是当前限制。最大论文 527 张 Card，实际输入 192,572 tokens，调用正常结束。

当前主要风险从“批次能否运行”转为“少量 LLM 输出可能违反选择数量契约”。本轮首次筛选有 1/20 发生该问题，显式重试后通过；失败历史保留，没有静默修改结果。

## 冻结输入和运行

综述主题：`三峡枢纽通航能力的提升方法`

输入清点：`paper-pool-836files-20260803-v3`

Card 批次：`paper-pool-card-preparation-836files-20260803-production20-v1`

- 选择论文：20
- 最大并发：2
- 相邻启动间隔：2 秒
- MinerU 模式：`precise`
- MinerU 模型：`vlm`
- 批次状态：`completed_partial`
- 成功：20
- 失败：0
- 未启动：0
- 总耗时：约 296 秒

增量清点：`paper-pool-836files-20260803-v4`

- 来源文件：836
- 唯一内容：834
- Card 就绪：68，较 v3 增加 20
- 待制卡：762，较 v3 减少 20
- 规范来源阻断：4
- 重复别名：2

主题筛选首轮：`paper-pool-screening-836files-20260803-production20-v7`

- 成功：19
- 契约失败：1
- 失败代码：`scope.selection_limit_exceeded`

显式续跑：`paper-pool-screening-836files-20260803-production20-v8`

- 复用已有成功筛选：67
- 重跑本轮失败：1
- 当前已筛选：68
- 当前筛选失败：0
- 已有 Card 但待筛选：0

## Card 审计

修复后的 20 篇最新代际共生成 915 张 Card：

- 最少：5 张
- 中位数：17 张
- 最多：527 张
- 结构质量：20 篇均为 `silver`
- 三层覆盖率：20 篇均通过

全部为 `silver` 的主要原因不是正文覆盖失败，而是作者、单位、分类号等前置信息触发 `parse.unclassified_front_matter`。问题代码分布：

- `parse.unclassified_front_matter`：20 篇
- `parse.inferred_headings`：5 篇
- `parse.bilingual_title_scope_merged`：3 篇
- `parse.document_title_fuzzy_match`：1 篇

`三峡——葛洲坝枢纽通航建筑物联合运行方式浅析` 的选段只占 Markdown 约 67.5%，检查发现其后是同一 PDF 中另一篇无关文章，因此排除边界正确。

### 验证中发现并修复的问题

`三峡-葛洲坝联合调度系统闸室编排快速算法` 初次运行：

- Markdown：166 行
- 选定范围：第 1 至 14 行
- Card：1 张

原因是英文摘要使用 `<sup>Abstract：</sup>`，双语题名范围判断没有在检测摘要前清理 HTML 标签。修复后增加结构反例测试，并再次调用真实 MinerU：

- 选定范围：第 1 至 166 行
- block：58
- Card：16
- 三层覆盖率：均为 1.0
- 问题代码：`parse.bilingual_title_scope_merged`、`parse.unclassified_front_matter`

批次原检查点仍记录初次 generation；最新清点 v4 和后续筛选使用修复后的新 generation。

## 单篇筛选结果

| 论文 | Card | 结构 | 相关性 | 最终筛选 tokens |
|---|---:|---|---|---:|
| 三峡-葛洲坝两坝间乐天溪锚地安全运行管理浅探 | 5 | silver | peripheral | 2,457 |
| 三峡-葛洲坝两坝间航道汛期大流量下船舶航行安全影响因素分析 | 24 | silver | supporting | 6,532 |
| 三峡-葛洲坝枢纽引航道和船闸群协同调度的多目标混合启发式算法 | 73 | silver | core | 31,670 |
| 三峡-葛洲坝枢纽水域大风大雾下的通航管理 | 20 | silver | peripheral | 5,146 |
| 三峡-葛洲坝枢纽过闸船舶船型发展简析 | 17 | silver | peripheral | 5,370 |
| 三峡-葛洲坝枢纽通航作业的多目标调度优化 | 527 | silver | core | 193,158 |
| 三峡-葛洲坝枢纽通航态势统计分析 | 11 | silver | peripheral | 4,480 |
| 三峡-葛洲坝梯级枢纽通航二十年创新发展与实践 | 37 | silver | core | 11,864 |
| 三峡-葛洲坝梯级水库兼顾航运需求的调度方式 | 25 | silver | core | 11,573 |
| 三峡-葛洲坝水域调度计划编制问题研究 | 13 | silver | core | 6,062 |
| 三峡-葛洲坝联合调度系统闸室编排快速算法 | 16 | silver | core | 6,096 |
| 三峡-葛洲坝船舶监管系统在船舶过闸管理中的应用 | 9 | silver | peripheral | 3,065 |
| 三峡-葛洲坝船闸通航压力缓解措施 | 9 | silver | core | 3,015 |
| 三峡-葛洲坝船闸通过4.5m吃水船舶的交通组织研究 | 32 | silver | core | 9,826 |
| 三峡GPS船舶远程过闸申报升级方案 | 12 | silver | supporting | 3,552 |
| 三峡——巨型水库航运安全新对策 | 6 | silver | peripheral | 3,740 |
| 三峡——葛洲坝枢纽水运新通道联合运行方式研究 | 17 | silver | core | 5,932 |
| 三峡——葛洲坝枢纽通航建筑物联合运行方式浅析 | 8 | silver | supporting | 3,778 |
| 三峡—葛洲坝两坝间汛期大样本实船试验分析研究 | 29 | silver | supporting | 9,982 |
| 三峡—葛洲坝两坝间河段通航技术研究 | 25 | silver | core | 8,816 |

相关性分布：

- `core`：10
- `supporting`：4
- `peripheral`：6
- `exclude`：0

20 篇最终筛选共使用 336,114 tokens。所有最终请求均为 `finish_reason=stop`，`reasoning_content_chars=0`，响应模型为 `deepseek-v4-pro`。

最大输入论文为 `三峡-葛洲坝枢纽通航作业的多目标调度优化`：

- Card：527
- Prompt：192,572 tokens
- Completion：586 tokens
- 总计：193,158 tokens
- 请求耗时：约 28.3 秒

该论文占本批 Card 和 Token 的一半以上，但仍能作为单篇任务一次提交，不需要按章节拆分。

## LLM 契约失败判断

`三峡-葛洲坝枢纽水域大风大雾下的通航管理` 首次被判为 `peripheral`，模型选择了 3 张 Card，而契约上限为 2，因而失败。原始响应、解析响应、Token 和失败代码均已保存。

本轮不放宽 peripheral 上限，因为 3 张中摘要 Card 与具体建议 Card 存在信息重叠。显式新 run 重试后模型保留 2 张更具体的 Card，成功通过合同。

定性判断：这是一次可审计的随机契约违约，不足以证明现有上限不合理；但在后续数百篇处理中，人工启动重试会增加操作成本，应继续统计出现频率。

## 下一阶段参数

下一批可以扩大到 50 篇，仍采用：

- MinerU 最大并发：2
- 启动间隔：2 秒
- 每篇独立发布 Card
- 每批完成后生成新 inventory
- 新增 Card 单篇执行 DeepSeek 筛选
- 筛选合同失败保留原 run，并通过新 run 显式续跑

暂不提高并发，也不修改主题相关性和选卡上限。累计完成至少两个 50 篇批次后，再根据 MinerU 失败率、LLM 契约失败率和总耗时决定是否调整。
