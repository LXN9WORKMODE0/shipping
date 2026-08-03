# 全论文池低交互测试执行方法

## 目的

完成论文池全部来源的 PDF 转 Markdown、Card 生成和单篇主题筛选，同时避免由 Codex 逐批轮询和逐篇调度造成不必要的额度消耗。

## 执行原则

1. PDF 到 Card 与 DeepSeek 单篇筛选分阶段执行，不交替运行。
2. 每个阶段使用一个冻结的论文池清点代际，长运行内部不反复重建清点。
3. Card 长运行不设置 `max_papers`，由现有检查点机制持续处理清单内全部待制卡论文。
4. 每篇论文保持独立终态：`completed` 或带明确问题码的 `failed`；失败不阻断其他论文。
5. 网络和供应商失败只通过显式续跑重试，不复用失败论文的旧 Card，不修改内容约束。
6. Codex 只在阶段结束后读取聚合结果，或在长运行停止时分析新的系统性问题。

## 阶段一：全量 Card

输入为冻结的论文池 Inventory。调用一次 `paper-pool-prepare-cards`，不传 `--max-papers`：

```powershell
python main.py paper-pool-prepare-cards `
  --inventory-run-id <inventory_run_id> `
  --topic "三峡枢纽通航能力的提升方法" `
  --workspace workspace `
  --run-id <card_run_id> `
  --max-workers 2 `
  --min-start-interval-seconds 2 `
  --pdf-provider mineru `
  --mineru-model-version vlm `
  --mineru-language ch
```

运行期间只写本地检查点：

- `runs/card_runs.jsonl`：逐篇终态
- `manifest.json`：紧凑进度
- `control/request.json`：暂停或取消请求

阶段结束后统一执行一次 Inventory，对账 Card 就绪、失败、重复和不支持格式。

## 阶段二：完整性审计

只读取当前发布代际，聚合检查：

- 原文行到 Region、Region 到 Block、Block 到 Card 三层覆盖率
- 低 Card 数和低论文作用域比例
- 解析质量等级和问题码频次
- 零 Card、失败和身份歧义论文

只有新的系统性解析问题才进入代码修改。修正后只重制受影响论文，再发布新的 Inventory；不因单篇内容质量差而加入静默修补规则。

## 阶段三：DeepSeek 单篇筛选

基于 Card 阶段最终 Inventory，一次运行全部待筛选论文。论文之间保持隔离；失败响应写入账本，其他论文继续。结构约束失败通过显式续跑重试，不截断模型选择、不放宽既定 Card 数限制。

## 阶段四：最终对账

最终报告必须覆盖全部 836 个来源文件，并给出：

- 唯一内容数、重复来源数
- Card 成功数和失败数
- DeepSeek 筛选成功数和失败数
- 不支持格式和身份歧义终态
- 覆盖率异常、问题码聚合及受影响论文
- 使用的 Inventory、Card 和 Screening 运行代际

## 当前执行基线

- Inventory：`paper-pool-836files-20260803-v10`
- Card 就绪：317 篇
- 待制卡：513 篇
- 固定阻塞：4 篇
- 重复来源：2 篇
- 已筛选：317 篇

下一 Card 长运行从 v10 的 513 篇待制卡论文开始。此前单篇 MinerU 转换失败会作为该冻结清单的一部分重新尝试。
