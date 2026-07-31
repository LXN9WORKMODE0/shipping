# 文献综述自动化 Pipeline 架构调整工作总结

## 1. 文档范围

本文总结自收到《文献综述自动化 Pipeline 架构调整任务》之后开展的工作，
包括：

- 对原架构问题的重新判断；
- 新数据流和模块的设计；
- Paper Understanding、Research Landscape、Review Framework、章节知识包、
  章节写作、Claim审计和A/B/C评价的实现；
- 14篇真实论文的逐阶段验证；
- 实施过程中暴露的问题和修正；
- 最终架构结论；
- 当前仍未解决的疑问与难题。

原始任务文档：

`docs/LLM_review_pipeline_architecture_adjustment.md`

实施计划：

`docs/superpowers/plans/2026-07-30-research-understanding-and-rich-writing.md`

## 2. 起点：为什么要调整原流程

在收到该任务时，项目已有以下基础能力：

```text
PDF
-> Markdown
-> Card
-> 单篇主题分析
-> Evidence
-> 跨论文主题综合
-> 综述提纲
-> 正文
-> 引用和审计
```

此前已经反复增强Markdown到Card的稳健性、完整性审计、论文级隔离、
Token规划、大论文分章处理、回查机制和人工报告。到架构调整任务开始时，
MD-to-Card可以视为“基本可用但不完美”，继续堆叠标题识别规则已经不是主要矛盾。

新的核心判断是：

> Evidence适合证明和引用，不适合作为综述写作的唯一认知输入。

Evidence会保留局部事实，却压缩掉以下内容：

- 研究问题与背景；
- 方法、数据和结论之间的论证关系；
- 论文贡献边界；
- 仿真、算法测试、系统设计、工程实施和建议之间的层次；
- 论文之间的互补、分歧和演化关系；
- 当前样本文献没有覆盖的问题。

因此，原来的：

```text
Card -> Evidence -> 综述
```

被调整为实验性新链路：

```text
Markdown + Card + Evidence
-> Paper Understanding
-> Research Landscape
-> Review Framework
-> Chapter Knowledge Package
-> Chapter Writing
-> Post-writing Claim Audit
```

原有Card、Evidence、Topic Review和Topic Synthesis均保留，不直接重构或删除。
新链路先以CLI和不可变运行目录并行建设，只有A/B/C评价通过后才允许接入默认
Pipeline和UI。

## 3. 总体设计原则

### 3.1 单篇理解与跨论文判断分离

Paper Understanding一次只处理一篇论文，描述论文自身的问题、方法、结果、
验证水平、限制和综述用途。

它不生成“与其他论文的关系”。论文之间的互补、分歧和演化统一交给
Research Landscape处理，避免单篇任务在看不到完整语料时猜测跨论文关系。

### 3.2 Understanding负责理解，Evidence负责证明

新链路没有把Understanding做成更长的Evidence：

- Understanding负责解释论文结构和研究意义；
- Card负责定位原文；
- Evidence负责已经过严格提炼的事实锚点；
- 完整Markdown保留未进入Evidence的上下文；
- 写后Claim审计负责重新验证正文。

### 3.3 定向样本缺口不等于领域空白

本次14篇论文被标记为：

```text
corpus_scope = targeted_sample
```

因此程序只允许输出`corpus_gaps`，即“本次语料没有回答什么”，不允许宣称
“整个领域不存在研究”。只有经过系统检索和质量评价的语料才可能讨论
field gap candidate。

### 3.4 每个阶段使用显式运行ID和不可变产物

所有阶段均遵守：

- 不按目录时间猜“最新运行”；
- 上游运行ID显式传入；
- manifest、输入哈希、模型配置和正式输出可重放；
- 失败运行保留请求、响应和失败账本；
- 新重试使用新run ID；
- 失败不覆盖旧结果；
- 不通过截断、删除字段或补写模型输出使结果“看起来成功”。

### 3.5 新链路先验证，不提前接UI

实施计划明确设置质量门槛：

1. B方案的核心事实支持率不得低于直接全文写作A；
2. B的结构和跨论文比较必须优于Evidence-only方案C；
3. B应减少人工修改时间；
4. 未通过时不接入默认Pipeline和UI。

## 4. 实施阶段与结果

## 4.1 Paper Understanding

### 实现内容

新增：

- `config/research-understanding-default.json`
- `research_understanding_contracts.py`
- `research_understanding.py`
- `research_understanding_report.py`
- `llm-paper-understanding` CLI

每篇论文输入：

```text
当前Card代次
+ 完整normalized/document.md
+ 全部Card投影
+ 显式指定Topic Review中的Evidence
+ 主题和题录身份
```

主要输出：

- 研究问题；
- 研究对象和数据来源；
- 方法；
- 多项贡献；
- result type；
- validation level；
- evidence strength及理由；
- 作者明确限制和审阅推断限制；
- 适合进入综述的用途；
- 论文自身未回答的问题。

模型不生成对象ID。所有Understanding、问题、方法、贡献和限制ID由程序根据
论文身份、规范化内容和来源Card确定性生成。

### 14篇真实验证

结果：

- 14/14篇均有接受运行；
- 所有运行状态为`completed`；
- 所有Card和Evidence引用有效；
- 仿真结果未标为工程实施；
- 建议未标为经验事实；
- 长论文没有退化成少量Evidence复述。

最大单篇输入：

- 257张Card；
- 82,386字符Markdown；
- 142,738输入Token；
- 加输出预算和安全余量后占100万上下文约18.6%。

首轮暴露的问题包括：

- 模型生成无法绑定贡献的综述用途；
- 已上线系统和后续建议混在同一贡献中；
- 已试行措施被误标为建议；
- 工程实施与概念验证水平不一致。

处理方式是强化合同和Prompt后重新调用，不改写旧输出。

验证记录：

`docs/research-understanding-14papers-validation-20260730.md`

## 4.2 Research Landscape

### 实现内容

新增：

- `research_landscape_contracts.py`
- `research_landscape.py`
- `research_landscape_report.py`
- `llm-research-landscape` CLI

输入是显式列出的14个Understanding运行，输出：

- 研究维度；
- 跨论文关系；
- 语料内研究关注演化；
- 明确分歧；
- 本次语料缺口；
- 未映射论文。

### 真实验证结果

最终接受运行：

`landscape-14papers-20260730-v9`

结果：

- 论文覆盖14/14；
- 研究维度7个；
- 跨论文关系8条；
- 年代关注阶段4个；
- 明确分歧0项；
- 本次语料缺口4项；
- 未映射论文0篇；
- 领域缺口候选0项；
- 输入40,842 Token；
- 输出4,111 Token。

### 主要失败与修正

Landscape经历了v1到v9。暴露的问题包括：

- 把一篇论文未讨论某问题误写成与另一篇论文对立；
- 根据发表年份虚构论文继承关系；
- dimension列出论文却没有引用该论文自己的贡献；
- 已进入维度的论文又被列为未映射；
- 把解释层次差异、单项方案与组合方案误写成分歧；
- API传输`IncompleteRead`。

最终关键修正不是继续写Prompt，而是生成动态JSON Schema：论文一旦进入维度、
关系、年代条目或分歧，就必须绑定该论文自己的贡献。

残余边界是：Landscape中的“互补”“共同构成”等关系仍是模型综合判断，
不是论文原话，写作时不能直接升级为因果事实。

验证记录：

`docs/research-landscape-14papers-validation-20260730.md`

## 4.3 Review Framework

### 实现内容

新增：

- `review_framework_contracts.py`
- `review_framework.py`
- `llm-review-framework` CLI

Framework从Landscape、综述目标和题录生成章节计划。模型负责：

- 工作标题；
- 中心问题；
- 每节问题和用途；
- dimension组合；
- 比较任务；
- 争议和语料限制。

程序确定性派生：

- 章节ID；
- 论文ID；
- contribution ID；
- citation key；
- 题录状态；
- 未使用论文。

### 验证结果

最终接受运行：

`framework-14papers-20260730-v2`

结果：

- 章节9个；
- 正文章节7个；
- 维度覆盖7/7；
- 论文覆盖14/14；
- 未进入正文论文0篇；
- 题录问题0项；
- 输入5,510 Token；
- 输出2,859 Token。

首轮Framework中，比较任务点名了本节知识包没有纳入的论文。修正后，
任何比较任务点名的论文都必须由本节绑定的dimension或controversy提供，
写作阶段不能偷偷增加不可见论文。

验证记录：

`docs/review-framework-14papers-validation-20260730.md`

## 4.4 Chapter Knowledge Package

### 实现内容

新增：

- `chapter_knowledge_package.py`
- 章节知识包测试；
- 不可变知识包运行目录。

每章知识包确定性装配：

```text
章节问题和比较任务
+ 相关Paper Understanding
+ 相关论文完整Markdown
+ 全部Card
+ Evidence和逐字引文
+ 题录和citation key
+ Landscape关系、分歧和语料缺口
```

Token预算直接按最终写作system prompt、完整知识包和动态输出Schema计算：

```text
输入Token + 最大输出Token + 安全余量 <= 上下文窗口
```

超过预算时明确失败，不截断Markdown、不跳过论文、不自动摘要、不切批。

### 9章验证结果

- 9/9知识包通过；
- 最大输入为结论章479,143 Token；
- 加输出和安全余量后约占100万上下文52.2%；
- 当前14篇不需要截断或拆分。

其中一个中等难度章节包含：

- 5篇完整Markdown；
- 491张Card；
- 21条Evidence；
- 5份Understanding；
- 5条题录；
- 2个Landscape维度；
- 3条跨论文关系；
- 1项语料缺口；
- 42个冻结文件哈希。

验证记录：

`docs/chapter-knowledge-package-14papers-validation-20260730.md`

## 4.5 章节写作与全文装配

### 实现内容

新增：

- `review_writing_contracts.py`
- `review_writing.py`
- `review_writing_report.py`
- `llm-review-writing` CLI

每章单独调用模型，只读取当前章节知识包。全部章节成功后，程序才确定性装配：

- 完整正文；
- 资料范围；
- 参考文献；
- 论文和引用统计；
- 最终编辑状态。

正文只能使用citation key数组，不能显示Evidence、Card、Material、Contribution、
Package或Section等机器ID。

### 真实结果

接受运行：

`writing-14papers-20260730-v2`

结果：

- 9章；
- 57段；
- 14篇论文全部被引用；
- 148次段落引用；
- 正文约25,098字符；
- 输入1,934,876 Token；
- 输出16,109 Token；
- 9章均成功；
- 正文机器ID为0。

首轮v1的9次API均正常返回，但第3章存在空citation keys，因此没有发布全文。
进一步审查还发现模型会把`ref_...`直接写入正文，再由程序追加引用。

修正方式：

1. Prompt明确citation key只能放在数组；
2. 验证器拒绝正文中的各种机器ID；
3. system prompt哈希进入知识包重放条件；
4. Prompt变化后旧知识包失效；
5. 重新生成9个知识包并重新执行全部章节。

v2只是“待Claim审计草稿”，最终编辑被标记为：

`not_run_audit_required`

验证记录：

`docs/review-writing-14papers-validation-20260730.md`

## 4.6 Post-writing Claim Audit

### 初始严格方案为何失败

最初要求模型逐字复制：

- Claim文本；
- Evidence ID；
- Card ID；
- 逐字引文；
- 来源位置。

v1到v4连续暴露：

- JSON格式错误；
- ID复制错误；
- 引文复制不一致；
- 输出规模过大；
- 传输和重试不稳定。

结论是：让模型同时做语义判断和精确数据搬运不可靠。

### 最终实用方案

程序负责：

- 确定性拆句；
- 生成稳定Claim ID；
- 保留原Claim文本；
- 根据citation key和来源层级展开Evidence、Card或Markdown；
- 统计发布门禁。

模型只负责：

- Claim类型；
- core/supporting重要性；
- supported/qualified/unsupported；
- Evidence/Card/Markdown来源层级；
- result type是否写大；
- validation level是否写大；
- 判断理由。

Claim分为：

- `factual`：外部可核验事实；
- `review_synthesis`：只描述本次语料边界或纯规范性建议；
- `navigation_synthesis`：结构导航。

只有事实Claim进入事实支持率和核心unsupported发布门禁。

### 报告和运行机制

实现了：

- 原始响应存档；
- 失败账本；
- Token汇总；
- 完整响应离线重放；
- 复用成功章节、只重试失败章节；
- 保守规范化标记；
- 人工报告只展开风险项；
- 每个来源最多显示3条预览；
- 完整来源保留在JSON。

人工Markdown报告从最初5,118行压缩到557行。

### 最终审计结果

接受运行：

`claim-audit-14papers-20260730-v12`

结果：

- Claim共287条；
- 事实Claim 230条；
- 综述内部综合52条；
- 导航Claim 5条；
- supported 196条；
- qualified 21条；
- unsupported 13条；
- 核心unsupported 11条；
- result type写大14条；
- validation level写大14条；
- 总Token 2,053,385；
- `publishable = false`。

11条核心不支持多数不是明显错误，而是结论章形成了跨论文归纳，却没有在对应
段落提供可展开的引用。内容可能合理，但审计链无法证明，因此按合同阻断发布。

验证记录：

`docs/review-claim-audit-14papers-validation-20260730.md`

## 4.7 A/B/C真实评价

### 实验方案

- A：同一14篇完整Markdown和题录，一次直接写作，再做Claim审计；
- B：完整新链路，再复用v12 Claim审计；
- C：旧Evidence-only写作，再做Claim审计。

接受评价运行：

`review-evaluation-abc-14papers-20260730-v4`

### 自动指标

| 指标 | A 直接全文 | B 新链路 | C Evidence-only |
|---|---:|---:|---:|
| 正文字符 | 4,629 | 20,001 | 3,943 |
| 论文覆盖率 | 100.00% | 100.00% | 92.86% |
| 核心维度覆盖率 | 100.00% | 100.00% | 100.00% |
| 跨论文比较段落比例 | 23.53% | 87.72% | 33.33% |
| 事实Claim支持率 | 97.06% | 85.22% | 54.10% |
| 核心事实支持率 | 95.83% | 85.45% | 97.06% |
| 核心不支持 | 0 | 11 | 0 |
| result type写大 | 0 | 14 | 0 |
| validation level写大 | 1 | 14 | 1 |
| 引用论文匹配率 | 96.46% | 79.94% | 74.07% |
| 语料缺口越界 | 0 | 0 | 0 |
| 结构评分 | 3/5 | 5/5 | 2/5 |
| 比较评分 | 2/5 | 5/5 | 1/5 |

### Token

| 方案 | 请求数 | 总Token |
|---|---:|---:|
| A | 2 | 709,560 |
| B | 34 | 4,633,944 |
| C | 124 | 1,317,354 |

### 结果解释

A的自然输出约4,600字。它短、结构一般、跨论文比较有限，但事实支持率最高、
引用匹配最好、成本最低。

B约2万字，结构和比较显著最好，证明认知层和章节知识包确实能让模型写出更丰富
的研究综述。但丰富上下文同时诱发更多跨论文归纳、关系推断和结论扩展，
事实支持率下降，并出现11条核心不支持。

C结构和总体事实支持率最差。其核心事实支持率高，是因为它只有较少核心Claim，
不能据此认为C优于A或B。

既定硬门槛：

```text
B核心事实支持率 >= A
```

实际为：

```text
85.45% < 95.83%
```

最终评价状态：

`fail_automatic_gate`

完整记录：

`docs/review-writing-abc-14papers-20260730.md`

## 5. Git实施时间线

| 提交 | 内容 |
|---|---|
| `1bb83a7` | 形成Research Understanding详细实施计划 |
| `33361c5` | Paper Understanding合同 |
| `baef64a` | Paper Understanding Runner |
| `50842f7` | 记录试验Token预算 |
| `8d4f7b1` | 强化综述用途与贡献绑定 |
| `9df2f45` | Paper Understanding语义验证 |
| `d53729a` | Research Landscape阶段 |
| `671174c` | Review Framework与来源绑定 |
| `50a590a` | 不可变章节知识包 |
| `0f127f7` | 章节写作和全文装配 |
| `5a9e107` | 写后Claim审计 |
| `09e9405` | A/B/C写作策略评价 |

当前分支：

`codex/synthesis-contract-repair`

上述提交均已推送远端。

## 6. 这次改造真正证明了什么

### 已证明

1. **完整论文认知层可以工程化。**
   14篇短、中、长论文都能生成来源绑定的Understanding，且能区分仿真、算法、
   系统设计、工程实施和建议。

2. **跨论文图谱和Framework可以显式化。**
   研究维度、关系、语料缺口和章节任务不再全部埋在单次写作Prompt中。

3. **章节知识包可以完整组装。**
   当前14篇规模下，无需截断即可把相关全文、Card、Evidence和理解对象交给模型。

4. **分章写作能显著改善结构和跨论文比较。**
   B方案结构、比较和篇幅明显优于A、C。

5. **写后审计可以发现“内容合理但无法证明”的结论。**
   Claim级发布门禁、来源回填、失败重放和风险报告已经闭合。

6. **复杂链路并不自动带来更可靠正文。**
   B更丰富，但事实支持率低于直接全文写作A，且成本明显更高。

### 没有证明

1. 没有证明B方案适合作为默认自动写作Pipeline。
2. 没有证明生成的2万字正文可直接发表。
3. 没有证明14篇定向样本代表完整研究领域。
4. 没有证明Claim审计本身等同于人工专家审稿。
5. 没有证明Paper Understanding越详细，最终写作一定越好。
6. 没有证明更长正文比紧凑的直接写作草稿更有使用价值。

## 7. 当前架构结论

按原实施计划，B未通过自动硬门槛，因此：

> 不进入Phase 5，不把新B链路接入默认Pipeline或现有UI。

当前更合理的产品边界是：

```text
PDF/Markdown/Card和论文池
        |
        +-> 可选Paper Understanding
        |
        +-> 可选Research Landscape和Framework
        |
        +-> 完整Markdown直接写作
        |
        +-> 写后Claim审计、引用定位和风险报告
```

也就是说，项目应优先成为：

- 论文处理和材料准备系统；
- 显式研究认知与组织工具；
- 直接写作的输入准备器；
- 写后事实和引用审计器。

不应继续默认追求“链路越长、上下文越丰富、自动生成正文越长”。

## 8. 当前仍有的疑问

## 8.1 产品目标到底是什么

目前存在两种不同产品方向：

### 方向一：自动写出接近成稿的综述

这要求继续解决：

- 引用和跨论文结论绑定；
- 审计后自动修订；
- 写作长度与事实密度控制；
- 反复修订的稳定性；
- 人工修改成本。

### 方向二：准备高质量材料，由用户或高级模型完成最终写作

这更符合当前验证结果：

- 论文池负责选择语料；
- Understanding和Landscape负责显化研究认知；
- Framework负责组织问题；
- 直接全文写作提供紧凑初稿；
- Claim审计负责指出风险。

当前尚未由用户明确最终选择哪一个作为产品主目标。自动门槛结果倾向方向二。

## 8.2 A和B是否进行了完全公平的写作比较

A自然生成约4,600字，B约2万字。A事实支持率更高，部分原因可能是：

- 篇幅更短；
- Claim更少；
- 跨论文推断更少；
- 只选择最确定的事实。

B承担了更复杂的综合任务，因此有更多出错机会。

本次实验比较的是两种方案的自然行为，足以决定“当前B不应成为默认链路”，
但不能回答：

> 如果强制A和B生成相同篇幅，哪一个最终更好？

若未来需要学术上更严格的比较，应做长度、章节数和输出预算匹配的对照实验。
从当前“尽快收束”的目标看，这项实验不是必需项。

## 8.3 Claim审计的准确率还缺少人工金标准

当前Claim审计由另一次LLM判断，并由程序约束引用和来源。它比自由审稿稳定，
但仍可能：

- 把合理综合误判为事实；
- 把事实误归为review synthesis；
- 误判source tier；
- 对core/supporting重要性判断不一致；
- 受Prompt和模型版本影响。

目前做了大量合同校验和保守规范化，但没有人工逐条标注的Claim测试集。
因此97.06%、85.22%等数值适合方案间比较，不应解释为绝对真实准确率。

## 8.4 审计发现问题后，谁来修改正文

当前系统能定位：

- unsupported核心结论；
- qualified表述；
- result type写大；
- validation level写大；
- 引用与Claim不匹配。

但尚未形成稳定的修订闭环：

```text
Claim风险
-> 补引 / 降格 / 删除 / 拆句
-> 重新审计
-> 发布
```

自动修订可能再次引入新事实或改变引用，形成迭代漂移。人工修订则需要更好的
编辑界面和差异展示。该问题是继续追求自动成稿时最关键的技术难题。

## 8.5 人工修改成本尚未测量

A/B/C配置中保留了：

- editing_minutes；
- edited_char_ratio。

目前状态为`pending`，没有伪造人工数据。由于B已失败于自动硬门槛，
该数据不会改变当前不接UI的决定，但仍影响对三种方案实际工作价值的判断。

例如：

- A是否只需扩写结构即可使用；
- B是否需要大量事实修订；
- C是否几乎需要重写。

这些问题只能通过真实用户编辑得到答案。

## 8.6 Markdown解析质量仍会传导到后续

新架构绕开了“Card必须承载全部论文信息”的限制，但没有消除上游OCR问题。

如果MinerU输出存在：

- 字符丢失；
- 表格错乱；
- 标题层级混乱；
- 公式损坏；
- 段落顺序错误；

完整Markdown、Card、Understanding和最终审计都会受到影响。当前14篇测试语料
可以运行，不代表复杂扫描PDF、双栏错序或严重OCR文献已解决。

## 8.7 规模扩大后，1M上下文仍可能不足

当前最大单篇Understanding只占约18.6%上下文，最大章节约52.2%上下文。
但如果综述扩大到几十或上百篇：

- 引言和结论无法读取全部全文；
- 多篇长学位论文会迅速超过1M；
- 全Markdown、Card和Understanding存在大量重复文本；
- 当前“不截断、不拆批”的v1策略无法继续。

届时需要重新设计：

- 章节论文选择；
- 层级摘要；
- 可回查的长文本索引；
- 跨章节共享认知；
- 超预算时的显式规划。

目前没有实现这些扩展策略。

## 8.8 模型成本和供应商选择尚未正式闭合

A、B、C Token差异很大，但实验未配置供应商价格表，因此没有费用结果。

此前比较过DeepSeek Flash和Pro，但本次架构实验主要使用Pro，尚不能严格回答：

- Understanding是否可以稳定改用Flash；
- Landscape和Framework是否必须使用Pro；
- Claim审计用Flash是否会降低准确性；
- 哪些阶段可以缓存或离线重放；
- 按实际价格哪条路径最有性价比。

如果继续产品化，需要建立模型级价格配置、阶段级模型选择和成本预算。

## 8.9 新能力与现有UI尚未整合

这是有意保留的状态，不是遗漏。由于B未通过门槛，以下内容没有接入项目UI：

- Understanding运行选择；
- Landscape运行选择；
- Framework；
- 直接全文写作；
- Claim审计；
- A/B/C评价。

现有UI仍主要服务论文池、Card、单篇分析和旧综合流程。

如果后续做UI，不应照搬完整B链路，而应先决定暴露哪些稳定能力。更可能的最小
界面是：

1. 论文池选择；
2. 可选单篇Understanding；
3. 直接全文写作；
4. Claim风险报告；
5. 引用定位和人工修订。

## 8.10 不可变运行数量和管理复杂度在增长

不可变运行保证了可审计性，但Landscape v1-v9、Claim Audit v1-v12等实验产生
大量运行目录。当前需要进一步考虑：

- 哪些运行被接受；
- 哪些只是失败历史；
- 如何归档；
- 如何标记当前推荐运行；
- 如何清理大体积原始响应而不破坏审计链；
- UI如何避免用户看到大量内部run ID。

项目已有运行治理和归档基础，但新阶段尚未全部接入统一管理。

## 9. 待解决难题的优先级

## P0：决定产品边界

需要明确：

> 项目接下来是继续追求自动成稿，还是收缩为材料准备、认知组织、直接写作和审计系统？

当前实验建议选择后者。若不先决定，后续UI和Pipeline设计会再次摇摆。

## P1：把稳定能力形成一条较短的可用流程

推荐候选：

```text
论文池
-> PDF/Markdown/Card
-> 可选Understanding
-> 完整Markdown直接写作
-> Claim审计
-> 人工修订
```

该流程应保持论文准备、写作和跨论文综合彼此独立，允许用户从论文池显式选择
可用论文，不因一篇解析或Understanding失败阻断全部任务。

## P1：设计Claim审计后的修订方式

需要决定：

- 只给人工风险清单；
- 允许用户逐条选择补引、降格或删除；
- 由模型生成修订建议但不自动应用；
- 自动应用后强制重新审计。

这是影响最终使用体验的关键环节。

## P1：建立小规模人工金标准

不需要人工审核全部287条Claim。可以选：

- 20条supported；
- 20条qualified；
- 全部核心unsupported；
- 部分result/validation写大项。

人工判断用于估计Claim审计的精度和主要误差类型。

## P2：确定是否需要长度匹配的A/B复验

只有在仍想证明B式写作有价值时才需要。若接受收缩结论，可以不做。

## P2：模型分层和成本配置

建立每个阶段的模型、上下文、输出预算和单价配置，重新比较Flash/Pro。

## P2：大规模语料策略

当论文数量超过当前14篇时，再依据真实超预算样本设计章节选择、层级认知和回查，
不要提前引入截断兜底。

## 10. 当前建议

当前不建议继续直接强化B式自动写作，也不建议立即把全部新阶段接入UI。

更合适的下一步是：

1. 接受A/B/C实验给出的收缩结论；
2. 保留Understanding、Landscape和Framework作为可选材料分析能力；
3. 以完整Markdown直接写作为默认轻量草稿路径；
4. 将Claim审计作为写后质量控制；
5. 设计简单的审计后人工修订流程；
6. 再决定哪些稳定能力进入现有论文池UI。

这既保留了本轮架构调整形成的可复用成果，也避免继续为“更长但更难证明”的
自动正文投入大量复杂度。
