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

## 5. 尚未完成的真实验证

计划使用最终筛选运行`paper-pool-all-20260804-screen-final-reconcile-v3`中的3篇core论文执行真实DeepSeek验证。调用需要发送每篇完整规范化Markdown和全部Card。该用途超出此前全池轻量筛选授权范围，因此尚未执行。

获得明确授权后，先完成3篇样本并审查输入规模、输出质量、失败类型、Token使用和下一层可用性；通过后再决定是否扩至30至50篇核心论文。

