# Paper Understanding批处理层实施记录

日期：2026-08-04

## 1. 背景与定位

全论文池生产验证已经完成823篇单篇主题筛选。下一层不直接把39597张Card送入跨论文综合，而是先对入选论文形成相互隔离、可追溯的Paper Understanding。

本轮只增加批处理控制面，不修改已经验证过的单篇Paper Understanding Prompt、JSON Schema、模型profile或Token参数。

## 2. 已实现链路

```text
冻结的全论文池筛选运行
→ 按core/supporting/peripheral显式选择
→ 严格重放每篇筛选运行与当前Card代际
→ 每篇独立运行Paper Understanding
→ 单篇成功或失败隔离
→ 父批次逐篇账本与成功运行清单
→ 供后续跨论文主题综合读取
```

新增命令：

```text
llm-paper-understanding-batch
```

默认只处理`core`。可重复传入`--relevance`扩大范围；`--max-papers`用于小批验证；`--resume-from-run-id`复用代际未变化的成功结果。

真实3篇验证后新增`--record-id`：可以按筛选账本ID冻结和排序显式论文集合。指定ID不存在、筛选不可用或不属于当前纳入等级时直接拒绝，避免按账本顺序抽样造成规模偏差。

## 3. 判断与状态逻辑

- `understood`：单篇理解运行完成，可进入下一层。
- `understanding_failed`：单篇失败，保留失败信息，不阻断其他论文。
- `pending_understanding`：论文已入选，但受本批上限影响尚未运行。
- `not_selected`：筛选完成，但不在本次指定相关性等级内。
- `screening_unavailable`：筛选未完成或源论文在上游被阻断。

续跑只复用同时满足以下条件的结果：当前论文仍在入选范围、Card generation未变化、旧单篇理解manifest仍为completed且代际一致。否则明确写入`resume_rejections.jsonl`并重新处理。

父批次只通过`successful_understanding_run_ids`向下一层发布成功产物。失败、未入选和筛选不可用论文仍保留在总账中，不会静默消失。

## 4. 已完成验证

- 新增批处理单元测试：4项通过。
- Paper Understanding、论文池筛选相邻测试：19项通过。
- 项目完整测试：601项通过，另有73个subtests通过。
- 已验证失败隔离、部分批次、续跑复用、未入选可见和意外子任务异常隔离。

## 5. 首轮真实验证

已使用最终筛选运行`paper-pool-all-20260804-screen-final-reconcile-v3`中的3篇core论文执行真实DeepSeek验证，运行ID为`paper-understanding-core-sample3-20260804-v1`。

- 3篇全部完成，0篇失败；
- 输入Card数分别为18、16、18；
- Token总量分别为13812、15404、15497，共44713；
- finish_reason均为stop，reasoning字符均为0；
- 输出均通过Schema和来源绑定校验；
- 对运营实测、算法仿真、系统设计与建议的基本区分成立。

这3篇均属于小论文，不能覆盖长上下文风险。本地统计317篇core论文Card数为：中位数23、P75为34、P90为165、最大527。下一轮应显式选择中位、P90和最大规模各1篇，而不是继续取账本前3篇。

## 6. 长上下文分层验证

获得显式授权后，运行`paper-understanding-core-scale3-20260804-v1`，分别选择中位、P90和最大规模论文：

| 论文 | Card | Prompt tokens | Completion tokens | 总tokens | 耗时 |
|---|---:|---:|---:|---:|---:|
| 升船机内船舶吃水检测系统研究 | 23 | 18,807 | 2,509 | 21,316 | 35.1秒 |
| 基于随机Petri网的三峡-葛洲坝通航系统联合调度研究 | 165 | 131,879 | 3,109 | 134,988 | 56.5秒 |
| 三峡-葛洲坝枢纽通航作业的多目标调度优化 | 527 | 419,125 | 5,938 | 425,063 | 132.3秒 |

三篇全部完成，finish_reason均为stop，未产生reasoning内容。最大论文形成5个研究问题、4类方法、9项贡献和4项限制，来源绑定覆盖摘要、问题建模和各算法章节；结果正确区分simulation、benchmark和conceptual，没有把仿真性能写成现场工程验证。

P90论文有140张Card未被最终认知对象引用，最大论文有470张未引用。这不是输入丢失：全部Card均冻结在单篇输入中，未引用Card也写入审计账本；其原因是Paper Understanding是有上限的论文级压缩对象，而不是逐Card复述。后续遇到具体主题缺口或高风险主张时，应通过Card回看和Evidence能力下钻。

## 7. 阶段结论

Paper Understanding批处理已通过小型、中位、P90和最大规模真实论文验证。现有1M上下文模型可以直接处理当前核心池中最大的527 Card论文，技术上不需要为上下文容量引入分章节兜底。

但最大论文单次消耗425,063 tokens。由此不能把“能够处理”直接等同于“应对317篇core无差别全部深度处理”。下一阶段应先冻结30至50篇真正进入当前综述结构的重点论文，运行Paper Understanding并建立跨论文主题综合；其余core和supporting论文保留筛选摘要与Card回看入口，在发现主题缺口时再增量纳入。
