# B2 Phase 1：知识包精简验收报告

日期：2026-07-31

分支：`codex/b2-review-pipeline`

冻结上游：`framework-14papers-20260730-v2`

验证范围：14 篇真实论文、9 个综述章节、Tier 1、纯本地离线运行

## 1. 定性结论

B2 Phase 1 已达到“可进入 Claim Ledger 开发”的工程验收条件：

- B1 历史产物和正式写作入口未被修改；
- 章节材料由显式的 Source Window 运行生成；
- Tier 1 不再展开完整 Markdown、全部 Card 和全部 Evidence；
- 认知投影、原文窗口和题录均能回溯到冻结上游；
- 贡献、直接材料、可绑定方法上下文和可绑定局限上下文没有静默缺口；
- 9 个章节的输入 Token 相比同源 B1 总计下降 73.9%；
- 所有运行均显式标记 `writing_ready=false`，目前不能进入正文写作。

这里通过的是“知识包精简和来源闭合”，不是 B2 写作质量。Claim Ledger、受约束写作和新 Claim 审计均尚未实现。

## 2. 本阶段实现

### 2.1 Source Window 合同

新增独立数据合同：

- `llm.selected_source_window.v1`
- `llm.source_window_selection.v1`
- `llm.source_window_run.v1`

每个窗口显式保存：

- 论文和章节身份；
- citation key；
- 原文文本和标题路径；
- 原文行、字符位置；
- material、contribution 和 Evidence 绑定；
- result type、validation level；
- 解析与质量标记；
- 确定性选择原因和来源指纹。

### 2.2 确定性选择逻辑

处理顺序如下：

1. 从冻结 Review Framework 读取章节论文和 contribution。
2. 读取冻结 Paper Understanding、Card、Evidence、Markdown 和题录。
3. 选择 contribution 直接绑定的材料及其 Evidence 引用材料。
4. 对每篇入选论文纳入所有存在原文绑定的方法和局限材料。
5. 方法和局限只作为上下文，不提升为结果证据。
6. 以 material 为单位确定性去重，保留全部选择原因和 contribution 绑定。
7. 校验原文坐标、citation key、贡献覆盖和直接材料覆盖。
8. 冻结所有输入字节并记录哈希，不允许覆盖同名运行。

无原文 material 绑定的 reviewer-inferred limitation 会保留在 Understanding 投影中，但不会伪造 Source Window，也不会被误报为“原文窗口丢失”。

### 2.3 Material Tier

- `tier_1`：只包含精选 Source Window，当前默认验收模式。
- `tier_2`：显式附加本章节相关论文完整 Markdown。
- `tier_3`：仅允许章节按语料顺序覆盖全部论文时显式附加全部 Markdown。

不存在自动升级或静默降级。

### 2.4 B2 预览知识包

新增独立运行根目录 `_chapter_knowledge_packages_b2/runs`，包内只包含：

- Section Task；
- 精简 Understanding 投影；
- Selected Source Windows；
- Citation Metadata；
- Token 预算和来源清单。

包内不包含 B1 的 `full_markdown`、`cards`、`evidence_units` 展开字段。由于 Claim Ledger 尚未加入，Schema 和 manifest 均强制：

```text
pipeline_generation = B2.phase1
claim_ledger_included = false
writing_ready = false
```

### 2.5 显式 CLI

新增：

```text
source-window-selection
chapter-knowledge-package-b2-preview
```

两个入口均要求显式上游 run ID，并生成不可变运行、输入快照、manifest、审计文件和中文人工报告。

## 3. 实施中暴露的问题

### 3.1 首次 Smoke Test 的哈希失败

首个运行：

```text
b2-source-window-phase1-section-3-smoke-20260731
```

在 Windows 上以文本方式写入冻结 JSON，实际文件被转换为 CRLF，但 manifest 使用 LF 字节计算哈希，导致正式加载器拒绝重放。

处理方式：

- 保留失败运行，不修改历史；
- 冻结文件改为直接写入参与哈希计算的原始 bytes；
- 使用新运行 `b2-source-window-phase1-section-3-smoke-20260731-v2` 验证重放。

这个问题证明不可变运行和重放校验实际阻止了“清单显示成功、文件身份不一致”的静默状态。

### 3.2 v1 上下文覆盖不足

第一次 14 篇验证只在方法或局限材料与入选 contribution 材料重叠时纳入上下文，出现：

- 10 个“论文章节”方法上下文缺口；
- 21 个“论文章节”局限上下文缺口。

该策略无法证明关键结果具有足够的“方法—结果—限制”语境，因此未接受 v1。

处理方式：

- 只要论文进入章节，就纳入该论文所有存在原文绑定的方法和局限材料；
- 上下文绑定到本章入选 contribution；
- 不把方法或局限升级为结果证据；
- 仅对存在可绑定原文的上下文执行缺失检查，避免 reviewer-inferred 内容产生假警报；
- 以全新的 v2 run ID 重建全部 9 章。

## 4. 真实语料验收结果

Source Window 运行：

```text
b2-source-window-14papers-section-{1..9}-20260731-v2
```

B2 预览知识包运行：

```text
b2-package-14papers-section-{1..9}-20260731-v2
```

B1 对照：

```text
package-framework-v2-writing-v2-section-{1..9}-20260730
```

| 章节 | 论文 | 窗口 | B1 输入 Token | B2 输入 Token | 降幅 |
|---:|---:|---:|---:|---:|---:|
| 1 | 14 | 185 | 478,793 | 124,975 | 73.9% |
| 2 | 2 | 13 | 21,617 | 10,379 | 52.0% |
| 3 | 5 | 44 | 54,684 | 26,737 | 51.1% |
| 4 | 5 | 56 | 171,955 | 40,314 | 76.6% |
| 5 | 5 | 90 | 255,580 | 58,386 | 77.2% |
| 6 | 7 | 84 | 204,753 | 56,050 | 72.6% |
| 7 | 3 | 69 | 234,058 | 47,065 | 79.9% |
| 8 | 3 | 25 | 34,250 | 17,285 | 49.5% |
| 9 | 14 | 185 | 479,186 | 124,436 | 74.0% |
| **合计** | **58 次论文入章** | **751** | **1,934,876** | **505,627** | **73.9%** |

所有章节均满足：

- `within_budget=true`；
- contribution 缺失数为 0；
- contribution 直接材料缺失数为 0；
- 可绑定方法上下文缺失数为 0；
- 可绑定局限上下文缺失数为 0；
- 未使用静默截断；
- 未自动丢弃论文；
- 未升级 Material Tier。

窗口构成：

| 类型 | 数量 |
|---|---:|
| `implementation_status` | 10 |
| `author_conclusion` | 25 |
| `result_context` | 179 |
| `method_context` | 461 |
| `limitation_context` | 66 |
| `problem_context` | 10 |

1,489 个重复候选事件被确定性合并到现有窗口中，选择原因没有丢失。

Token 构成：

| 组成 | Token |
|---|---:|
| Section Task | 5,865 |
| Understanding 投影 | 43,207 |
| Source Window | 431,775 |
| Expanded Markdown | 9 |
| Citation Metadata | 5,814 |
| 完整 Prompt | 505,627 |

真实加载器已重放全部 9 个 Source Window 运行和 9 个 B2 包运行，验证了输入哈希、输出哈希、运行身份和确定性重建。

## 5. 对验收结果的解释

这次结果证明：

1. B1 中完整 Markdown、全部 Card、全部 Evidence 的重复展开可以移除；
2. 去重后仍可保持原文级回查和显式来源绑定；
3. 即使补齐全部可绑定方法与局限上下文，Token 仍比 B1 下降 73.9%；
4. 1M context 不是本阶段限制，信息职责分离才是主要收益；
5. 当前成本主体已经变成 Source Window，占已统计组件 Token 的 88.7%。

最后一点也是下一阶段的重要约束：不能再通过无限增加原文窗口解决 Claim 控制问题。Claim Ledger 应围绕计划写出的 Claim 选择和批准来源，而不是把 Source Window 再次扩展成另一份全文。

## 6. 尚未解决的问题

1. 当前包没有 Claim Ledger，不能交给写作模型。
2. 方法窗口占 751 个窗口中的 461 个，后续需要以 Claim 为单位控制实际写作所需上下文。
3. reviewer-inferred limitation 没有原文来源时只能作为认知层判断，不能用于 direct fact。
4. 本阶段没有调用外部 LLM，也没有验证正文质量、引用质量或 Claim 风险。
5. B2 包使用独立运行根目录和 Schema，现有 UI 与默认 Pipeline 尚未接入，符合指南中“通过实验门槛前暂缓接入”的要求。

## 7. 下一步

进入 Phase 2：Claim Ledger。

开发顺序应为：

1. 定义 Claim 类型、强度、来源和拒绝合同；
2. 从章节任务、Understanding 和 Source Window 生成候选 Claim；
3. 程序校验论文、citation key、窗口归属和跨论文数量；
4. 输出批准与拒绝账本；
5. 保持 B2 知识包 `writing_ready=false`，直到 Approved Claim Ledger 完整接入并通过 14 篇真实语料验收。
