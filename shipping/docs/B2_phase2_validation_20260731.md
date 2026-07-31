# B2 Phase 2：Claim Ledger 验收报告

日期：2026-07-31

分支：`codex/b2-review-pipeline`

验证范围：14 篇真实论文、9 个综述章节、Tier 1、DeepSeek V4 Pro 非思考模式

## 1. 定性结论

B2 Phase 2 已达到“可以进入受约束章节写作开发”的工程验收条件：

- 每章先生成有限的候选 Claim，再由程序批准或拒绝；
- 所有需要原文支持的批准 Claim 都绑定明确论文、Source Window 和 citation key；
- 直接事实与作者观点的结果类型、验证层级必须来自绑定窗口；
- 比较和跨论文综合至少绑定两篇论文；
- 核心 Claim 被拒时整次 Ledger 失败，不会发布部分有效结果；
- supporting Claim 可以被拒并保留审计记录，不阻断整章；
- 9 章最终得到 68 条批准 Claim，核心拒绝为 0；
- 9 个正式 B2 v2 知识包都能从冻结输入重放；
- 最终写作包合计 514,920 输入 Token，比 B1 减少 73.4%；
- 所有包仍强制 `writing_ready=false`，尚未接入正文写作。

这里通过的是“写前 Claim 规划、程序裁决、来源绑定和不可变运行”，不是最终综述
正文质量。Claim 是否具有最佳学术价值、段落能否自然实现 Claim、写后是否出现计划
外事实，仍需 Phase 3 的受约束写作和新 Claim 审计验证。

## 2. 本阶段实现

### 2.1 独立 Review Framework B2

新增 `llm.review_framework.v2`，从冻结 B1 Framework 确定性投影：

- 全文目标 8,000 字；
- 每章目标字数和最大段落数；
- 核心 Claim、支持 Claim、跨论文综合 Claim 的数量上限；
- 高风险 Claim 类型；
- 结论强度上限；
- 章节间不重复的材料选择。

对应 CLI：

```text
review-framework-b2
```

正式运行：

```text
framework-b2-14papers-20260731-v1
```

9 章目标字数合计为 8,000 字，运行已通过确定性重放。

### 2.2 Claim 数据合同

新增合同：

- `llm.claim_ledger_draft.v1`
- `llm.claim_ledger_claim.v1`
- `llm.claim_ledger.v1`
- `llm.claim_ledger_run.v1`

支持的 Claim 类型：

| 类型 | 用途 |
| --- | --- |
| `direct_fact` | 单篇论文直接报告的事实或结果 |
| `comparative_fact` | 至少两篇论文之间的显式比较 |
| `cross_paper_synthesis` | 仅限当前纳入文献的跨论文归纳 |
| `author_view_summary` | 明确标注为作者观点 |
| `corpus_gap` | 仅描述本次语料的缺口 |
| `normative_recommendation` | 综述作者提出的建议 |
| `navigation_synthesis` | 只承担篇章导航，不引入事实 |

每条 Claim 必须保存：

- Claim 类型、正文计划、章节角色和重要性；
- 支持论文、Source Window 和 citation key；
- support mode；
- result type 和 validation level；
- 允许的表述强度；
- 必需限定语和禁止表述；
- 简短审计说明；
- 确定性 `claim_id`。

`claim_id` 由规范化 Claim 内容和来源绑定生成，不依赖批次编号。拆批或重跑不会仅因
顺序变化而改变同一 Claim 的身份。

### 2.3 程序裁决

LLM 只生成候选 Claim，是否批准由程序决定。主要规则包括：

1. 论文、窗口和 citation key 必须存在于本章冻结输入。
2. 需要原文窗口的四类 Claim，其 `supporting_papers` 必须恰好等于绑定窗口所属论文的去重集合。
3. citation key 必须按支持论文顺序一一对应。
4. 比较和跨论文综合至少需要两篇论文。
5. 跨论文综合必须以“本次纳入的文献”限定。
6. corpus gap 必须以“本次语料”限定。
7. 作者观点必须明确写“作者认为”或“作者提出”。
8. 建议必须明确写“建议”。
9. `direct_fact` 和 `author_view_summary` 必须绑定具有非空结果类型和验证层级的窗口。
10. 这两类 Claim 填写的结果类型和验证层级必须存在于绑定窗口。
11. Claim 类型、support mode 和允许强度必须使用固定映射。
12. 核心、支持和跨论文综合数量不得超过本章预算。

核心 Claim 只要有一条被拒，本次运行即失败。supporting Claim 被拒时，批准账本仍可
发布，但拒绝候选、错误代码和原候选会完整保存在审计目录。

### 2.4 不可变运行和审计

新增独立运行根目录：

```text
_claim_ledgers/runs
```

每次运行冻结：

- B2 Framework；
- Source Window Selection；
- 模型配置和 Claim Ledger 配置；
- 请求、原始响应、解析响应和调用结果；
- 批准与拒绝账本；
- 中文人工审阅报告；
- 输入、输出和 manifest 哈希。

同名 run ID 不允许覆盖。失败运行也保留，不能伪装成成功运行。

对应 CLI：

```text
llm-claim-ledger
```

### 2.5 B2 Phase 2 章节知识包

新增正式包合同 `llm.chapter_knowledge_package.v2` 和独立运行根目录：

```text
_chapter_knowledge_packages_b2_v2/runs
```

正式包包含：

- Section Task；
- 精简 Paper Understanding 投影；
- Selected Source Windows；
- Citation Metadata；
- Approved Claim Ledger；
- Token 预算和来源清单。

正式包不包含：

- rejected Claim；
- 完整 Markdown；
- 全部 Card；
- 全部 Evidence；
- B1 自由写作输入。

当前强制状态：

```text
claim_planning_ready = true
writing_ready = false
```

对应 CLI：

```text
chapter-knowledge-package-b2
```

## 3. 真实语料最终结果

最终接受的 Claim Ledger：

| 章节 | Ledger 运行 | 批准 | 拒绝 |
| ---: | --- | ---: | ---: |
| 1 | `claim-ledger-14papers-section-1-20260731-v1` | 3 | 3 |
| 2 | `claim-ledger-14papers-section-2-20260731-v7` | 8 | 0 |
| 3 | `claim-ledger-14papers-section-3-20260731-v1` | 8 | 0 |
| 4 | `claim-ledger-14papers-section-4-20260731-v1` | 10 | 0 |
| 5 | `claim-ledger-14papers-section-5-20260731-v4` | 11 | 1 |
| 6 | `claim-ledger-14papers-section-6-20260731-v2` | 9 | 1 |
| 7 | `claim-ledger-14papers-section-7-20260731-v1` | 6 | 2 |
| 8 | `claim-ledger-14papers-section-8-20260731-v3` | 7 | 1 |
| 9 | `claim-ledger-14papers-section-9-20260731-v2` | 6 | 1 |
| **合计** |  | **68** | **9** |

重要性分布：

| 重要性 | 数量 |
| --- | ---: |
| `core` | 33 |
| `supporting` | 35 |

类型分布：

| Claim 类型 | 数量 |
| --- | ---: |
| `direct_fact` | 25 |
| `comparative_fact` | 16 |
| `cross_paper_synthesis` | 6 |
| `author_view_summary` | 8 |
| `corpus_gap` | 7 |
| `normative_recommendation` | 6 |

最终程序化检查：

- 核心拒绝：0；
- 比较或跨论文 Claim 少于两篇论文：0；
- 需要原文支持的 Claim 论文与窗口集合不一致：0；
- 直接事实或作者观点缺少类型化窗口：0；
- result type 与绑定窗口不一致：0；
- validation level 与绑定窗口不一致：0；
- 9 个 Ledger 重放成功；
- 9 个 B2 v2 知识包重放成功。

正式知识包运行：

```text
b2-package-claims-14papers-section-{1..9}-20260731-v2
```

| 章节 | 计划输入 Token | 预算内 |
| ---: | ---: | --- |
| 1 | 121,895 | 是 |
| 2 | 12,213 | 是 |
| 3 | 28,552 | 是 |
| 4 | 43,301 | 是 |
| 5 | 61,090 | 是 |
| 6 | 57,584 | 是 |
| 7 | 48,272 | 是 |
| 8 | 19,435 | 是 |
| 9 | 122,578 | 是 |
| **合计** | **514,920** | **是** |

与历史输入规模对比：

| 方案 | 9 章输入 Token | 相对 B1 |
| --- | ---: | ---: |
| B1 完整知识包 | 1,934,876 | 基线 |
| B2 Phase 1，无 Claim Ledger | 505,627 | -73.9% |
| B2 Phase 2，含批准 Claim Ledger | 514,920 | -73.4% |

Claim Ledger 使 Phase 2 比 Phase 1 增加 9,293 Token，即 1.8%。这个增加换取了
显式的写前 Claim 边界，没有重新引入完整 Markdown、全部 Card 或全部 Evidence。

## 4. DeepSeek 调用与失败记录

最终接受的 9 次调用：

- 输入 Token：510,775；
- 输出 Token：23,527；
- 合计 Token：534,302；
- `finish_reason` 均为 `stop`；
- `reasoning_content_chars` 为 0，使用非思考模式。

包括调试失败在内，本阶段共保留 22 次不可变调用：

- 成功运行 13 次；
- 失败运行 9 次；
- 输入 Token：970,975；
- 输出 Token：60,333。

失败没有删除，分别暴露了：

1. `section_role` 枚举缺少实际需要的 supporting 角色。
2. 模型会创造 `plain_claim` 等合同外字段。
3. 类型、support mode 和允许强度之间的映射仅靠文字说明不够明确。
4. 模型会把自我纠错过程写入 `audit_notes`。
5. 模型可能超出 supporting Claim 数量上限。
6. 一次返回在 `finish_reason=stop` 时仍产生未闭合 JSON 字符串。
7. 模型会列入没有绑定窗口的支持论文。
8. 模型会遗漏限定语，导致核心 Claim 被程序拒绝。
9. 事实 Claim 可能填写绑定窗口并未提供的结果类型或验证层级。

对应处理不是放宽规则，而是：

- 完善 JSON Schema 和类型映射；
- 将动态数量上限前置为明确指令；
- 约束审计备注长度和内容；
- 增加论文集合与窗口集合的一致性校验；
- 增加类型化窗口及语义层级来源校验；
- 失败后使用新 run ID 重跑，保留旧运行。

## 5. 测试结果

B2 定向测试：

```text
60 passed
```

项目全量测试：

```text
481 passed, 73 subtests passed
```

Python 编译检查通过。

## 6. 当前边界和风险

1. 当前证明的是合同有效、来源可回溯和运行可重放，不等于 68 条 Claim 都是最佳学术判断。
2. 供应商接口目前使用 `response_format=json_object`，并非服务端严格 JSON Schema；程序端 Schema 和裁决仍是正式边界。
3. supporting Claim 被拒不会阻断章节，这是为了避免低价值细节拖垮综述生产，但拒绝记录必须保留。
4. 第 1 章只批准 3 条 Claim，是否足以支撑目标篇幅要由 Phase 3 写作实测判断，不能提前补造 Claim。
5. Source Window 仍占主要输入成本；Phase 3 写作应按批准 Claim 使用窗口，不应重新扩展为全文输入。
6. 当前未验证正文对 Ledger 的实现率、计划外事实、引用位置和语言质量。
7. UI 和默认全流程尚未接入 B2，符合实验方案在通过写作验收前保持隔离的要求。

## 7. 下一步

进入 Phase 3：受约束章节写作。

最小实施顺序：

1. 定义 B2 章节和段落合同，每段显式列出 `implemented_claim_ids`。
2. 写作模型只能实现 Approved Claim Ledger，不得自行增加核心事实。
3. 核心 Claim 必须全部实现；supporting Claim 可以不全部使用，但必须记录未使用项。
4. 引用必须属于该段实现 Claim 的允许 citation keys。
5. 写后执行确定性 Ledger 一致性检查。
6. 再调用审计模型识别计划外事实、来源缺失、表述写大和不当综合。
7. 用 14 篇、9 章生成完整 B2 草稿，与 B1 和直接全文 Prompt 进行匿名质量评价。
