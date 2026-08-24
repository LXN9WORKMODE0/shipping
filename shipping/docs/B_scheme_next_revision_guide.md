# 文献综述自动化 Pipeline：B方案下一阶段改造指南

## 1. 文档目的

本文用于指导下一阶段对现有B技术方案进行修改。

本轮工作的基本判断不是放弃以下已经验证有效的能力：

- Paper Understanding；
- Research Landscape；
- Review Framework；
- Chapter-level Writing；
- Post-writing Claim Audit；
- 不可变运行、来源绑定和失败保留机制。

这些能力已经证明可以显著改善综述的结构、跨论文比较和研究认知显式化。

当前需要修改的不是“是否保留认知层”，而是以下具体设计：

1. 章节知识包过重，多个信息层重复进入上下文；
2. 写作模型在没有预先约束Claim的情况下自由扩展；
3. Evidence主要在写后审计阶段发挥作用，未充分前移到写作规划阶段；
4. 合理综合、缺少引用绑定和真实事实错误在审计中区分不足；
5. 结论章与普通章节采用相近的生成方式，导致高层综合结论更容易越界；
6. A/B实验任务长度和复杂度不匹配，尚不足以证明认知层路线无效。

因此，下一阶段不应退回Evidence-only，也不应简单退回“完整Markdown直接写作”。

下一阶段目标是形成一个更轻、更可控的B2方案：

```text
论文池
→ Markdown / Card / Evidence
→ Paper Understanding
→ Research Landscape
→ Review Framework
→ 章节论文与材料选择
→ 精简章节知识包
→ Claim Ledger
→ 受约束章节写作
→ 分类型Claim审计
→ 人工修订
→ 重新审计
```

---

## 2. 对上一轮实验的重新解释

### 2.1 已经证明的内容

上一轮实验已经证明：

1. 完整论文认知层可以工程化；
2. 单篇理解和跨论文综合可以分离；
3. Research Landscape能够显式表达研究维度、关系和语料缺口；
4. Review Framework能够显式生成章节任务和比较任务；
5. 分章写作显著提高结构质量和跨论文比较程度；
6. Claim审计可以识别无法证明、引用不完整和表述写大的正文内容；
7. Evidence-only写作不适合作为高质量综述的主要生成方式；
8. 复杂链路必须通过显式合同、程序校验和不可变运行进行治理。

### 2.2 尚未证明的内容

上一轮实验没有证明：

1. Paper Understanding、Landscape和Framework不适合参与写作；
2. 直接全文写作在相同篇幅和相同任务复杂度下仍优于B；
3. B中全部unsupported Claim都属于事实错误；
4. 更丰富的研究认知本身会降低准确率；
5. 分章写作本身是造成引用和事实问题的原因；
6. B只能作为可选分析能力，不能继续形成自动写作链路。

上一轮B方案的失败应更准确地解释为：

> 当前“全量知识包 + 自由章节生成 + 写后集中审计”的具体实现没有达到默认发布门槛。

而不是：

> 认知层、研究图谱和分章写作方向失败。

---

## 3. B2方案的核心目标

### 3.1 保留结构和综合能力

继续保留B方案相对于A和C的优势：

- 明确的研究维度；
- 章节问题驱动；
- 跨论文比较；
- 论文关系显式化；
- 研究演化和语料边界；
- 章节级材料组织。

### 3.2 降低事实越界

重点减少：

- 将局部结果写成领域共识；
- 将算法性能写成实际工程效果；
- 将仿真结果写成实测结果；
- 将平台设计写成已实施成果；
- 将建议写成经验事实；
- 将语料缺口写成领域空白；
- 将模型综合判断写成论文直接结论。

### 3.3 降低上下文冗余

禁止继续把以下内容无差别全部装入同一写作上下文：

```text
完整Markdown
+ 全部Card
+ 全部Evidence
+ 全部Understanding
+ 全部Landscape
+ 全部Framework
```

B2必须区分：

- 用于规划的信息；
- 用于写作的信息；
- 用于引用的信息；
- 用于审计和回查的信息。

### 3.4 将事实控制前移

当前主要流程是：

```text
自由写作
→ 拆Claim
→ 审计
→ 阻断
```

B2应调整为：

```text
规划Claim
→ 校验Claim
→ 基于批准Claim写作
→ 再审计
```

### 3.5 保持人工可控

当前阶段不追求完全自动修订。

目标是：

- 程序定位风险；
- 模型提出有限修订建议；
- 人工选择补引、降格、删除或拆句；
- 程序应用变更；
- 强制重新审计。

---

## 4. B2总体架构

建议将系统明确拆成六个层级。

### 4.1 Source Layer

包括：

- PDF；
- normalized Markdown；
- 题录；
- 原始表格和图示定位信息；
- 解析质量报告。

职责：

- 保存论文原始材料；
- 提供最终回查来源；
- 不承担综述组织和生成职责。

### 4.2 Retrieval Layer

包括：

- Card；
- 原文坐标；
- 语义标签；
- 检索索引；
- 解析质量标记。

职责：

- 检索；
- 定位；
- 为章节材料选择提供候选片段；
- 为审计回填上下文。

Card不再承担“完整表达论文”的责任，也不默认全部进入写作上下文。

### 4.3 Cognition Layer

包括：

- Paper Understanding；
- Research Landscape；
- Review Framework。

职责：

- 解释论文研究问题、方法、结果、贡献和限制；
- 建立研究维度；
- 建立论文间互补、差异和演化关系；
- 规划章节任务；
- 形成比较问题；
- 明确语料边界。

认知层负责“如何理解和组织”，不直接作为事实证明。

### 4.4 Claim Planning Layer

新增核心对象：

## Claim Ledger

职责：

- 在生成正文前规划本节允许写出的核心Claim；
- 绑定支持论文和来源层级；
- 控制Claim的允许强度；
- 区分直接事实、跨论文综合和规范性建议；
- 将Framework中的章节任务转换为可审计写作单元。

这是B2相对于B1最重要的新增层。

### 4.5 Writing Layer

输入：

- 章节任务；
- 已批准Claim Ledger；
- 相关Paper Understanding；
- 精选原文上下文；
- citation key；
- 写作约束。

职责：

- 将已批准Claim组织成自然、连贯、有比较深度的章节；
- 不新增计划外核心事实；
- 不扩大Claim强度；
- 不自行改变result type或validation level；
- 不直接输出机器ID。

### 4.6 Audit and Revision Layer

包括：

- Claim拆分；
- 事实支持判断；
- 引用匹配；
- 结果类型检查；
- 验证水平检查；
- 语料边界检查；
- 风险分类；
- 人工修订；
- 重新审计。

职责：

- 检查正文是否忠实实现Claim Ledger；
- 识别写作模型在语言扩展中引入的新风险；
- 区分真实错误、缺引、合理综合和表述过强；
- 形成发布门禁。

---

## 5. 核心修改一：精简章节知识包

### 5.1 当前问题

当前章节知识包包含：

- 相关论文完整Markdown；
- 全部Card；
- 全部Evidence及逐字引文；
- 全部Paper Understanding；
- Landscape关系；
- 语料缺口；
- 题录；
- 章节任务。

这些内容存在明显重复：

```text
Markdown包含原文
Card再次保存原文片段
Evidence再次保存关键片段
Understanding再次概括原文
Landscape再次概括Understanding
```

该设计可能导致：

1. 相同观点因重复出现而获得虚假的注意力权重；
2. 不同抽象层级的表述相互叠加并逐级放大；
3. 长上下文中关键信息被大量重复材料稀释；
4. Token成本显著增加；
5. 写作模型难以区分“原文事实”和“模型综合”。

### 5.2 B2知识包结构

每章知识包默认只保留以下内容。

#### A. Section Task

包括：

- 章节标题；
- 本节中心问题；
- 必须回答的子问题；
- 必须完成的比较；
- 必须体现的分歧；
- 本节允许涉及的语料缺口；
- 禁止生成的结论类型；
- 目标篇幅。

#### B. Relevant Paper Understanding

只包含本节相关论文的：

- research question；
- study object；
- method；
- data source；
- main contribution；
- result type；
- validation level；
- limitations；
- suitable review use。

不默认重复输入完整单篇Understanding报告中的所有字段。

#### C. Selected Source Windows

由Card和Markdown检索装配的精选原文窗口。

每个窗口应包含：

- paper ID；
- citation key；
- 原文文本；
- 上下文标题；
- 原文位置；
- window type；
- result type；
- validation level；
- 是否包含限制信息；
- 解析质量标记。

默认窗口类型：

```text
problem_context
method_context
result_context
limitation_context
author_conclusion
implementation_status
```

对于关键结果，必须同时提供足够的“方法—结果—限制”上下文，不能只提供一个结论句。

#### D. Citation Metadata

只保留：

- citation key；
- paper ID；
- 论文标题；
- 年份；
- 文献类型；
- 题录状态。

#### E. Approved Claim Ledger

写作模型只围绕已经通过校验的Claim进行写作。

### 5.3 不应默认进入知识包的内容

以下内容保留在后台，可用于检索和审计，但不默认进入写作上下文：

- 全部Card；
- 与本章无关的完整Markdown；
- 所有Evidence；
- 全部逐字引文；
- 完整Landscape报告；
- 完整Framework报告；
- 失败运行；
- 原始模型响应；
- 重复的Paper Brief。

### 5.4 完整Markdown的使用方式

不要彻底禁止完整Markdown。

建议采用三级材料模式：

```text
Tier 1：精选原文窗口
默认写作材料

Tier 2：章节相关论文完整Markdown
仅在原文窗口不足或章节任务确实需要全文理解时加入

Tier 3：全部论文完整Markdown
仅用于直接全文基线、引言总览或明确的对照实验
```

知识包装配器必须记录使用了哪个Tier。

不允许静默从Tier 1自动升级到Tier 2或Tier 3。

---

## 6. 核心修改二：新增Claim Ledger

### 6.1 目的

Claim Ledger不是最终正文，也不是Evidence。

它回答：

> 这一章节具体准备写哪些可审计的判断，每个判断能写到什么强度，由哪些论文支持？

它是连接以下模块的中间合同：

```text
Research Framework
→ Source Materials
→ Writing
→ Audit
```

### 6.2 建议数据结构

```json
{
  "claim_id": "CLM-SECTION-001",
  "section_id": "SEC-003",
  "claim_type": "cross_paper_synthesis",
  "planned_claim": "本次样本文献主要从运行组织优化、船型适配和工程扩能三个层面讨论通航能力提升。",
  "section_role": "topic_sentence",
  "importance": "core",
  "supporting_papers": ["P001", "P004", "P008"],
  "supporting_source_windows": ["WIN-P001-003", "WIN-P004-011", "WIN-P008-007"],
  "citation_keys": ["ref_p001", "ref_p004", "ref_p008"],
  "support_mode": "corpus_level_inference",
  "result_type": "mixed",
  "validation_level": "mixed",
  "allowed_strength": "targeted_corpus_only",
  "required_qualifier": "本次纳入的文献显示",
  "prohibited_phrasings": ["学界普遍认为", "现有研究已经证明", "已形成共识"],
  "audit_notes": ""
}
```

### 6.3 Claim类型

至少支持：

#### direct_fact

单篇或多篇论文直接报告的事实。

#### comparative_fact

多篇论文之间可以直接比较的对象、方法、指标或结果。

要求：

- 明确比较口径；
- 不同研究对象和指标不能假定可直接等价；
- 比较句必须绑定所有参与比较的论文。

#### cross_paper_synthesis

基于多篇论文形成的样本内综合判断。

要求：

- 至少两篇独立论文；
- 明确标记为模型综合；
- 默认使用样本边界限定语；
- 不能升级为领域共识。

#### author_view_summary

对论文作者观点、建议或判断的归纳。

必须写成：作者提出、研究建议、文献认为或论文讨论。

不能写成：实践证明、已经实现或实测表明。

#### corpus_gap

仅描述本次纳入语料未回答的问题。

必须带有：本次语料、所纳入文献或当前样本文献。

#### normative_recommendation

系统、用户或综述作者提出的建议，必须与文献结论分离。

#### navigation_synthesis

仅用于章节结构、转折和内容导航。

### 6.4 Claim生成流程

```text
Framework Section
→ 提取章节必须回答的问题
→ 从Understanding和原文窗口生成候选Claim
→ 程序校验来源
→ LLM评估Claim类型和允许强度
→ 程序生成确定性Claim ID
→ 形成Approved Claim Ledger
```

### 6.5 Claim发布前校验

每条Claim至少检查：

1. 支持论文是否存在；
2. citation key是否有效；
3. 来源窗口是否来自对应论文；
4. direct_fact是否存在直接来源；
5. cross_paper_synthesis是否至少有两篇独立论文；
6. result type是否与表述匹配；
7. validation level是否与表述匹配；
8. 是否需要语料边界限定语；
9. 是否点名未进入知识包的论文；
10. 是否出现禁止表达。

未通过校验的Claim不得进入写作Prompt。

---

## 7. 核心修改三：受约束章节写作

### 7.1 写作模型的职责

写作模型负责：

- 组织章节逻辑；
- 连接已批准Claim；
- 比较论文；
- 形成自然段落；
- 减少逐篇复述；
- 统一术语；
- 控制重复。

写作模型不负责：

- 新增核心事实；
- 新增未批准的跨论文结论；
- 自行扩大Claim强度；
- 自行补充新论文；
- 自行生成citation key；
- 自行改变验证水平；
- 自行把建议改写为实践成果。

### 7.2 建议输出结构

每段输出不仅包括正文，还包括所实现的Claim ID和citation key数组。

```json
{
  "paragraph_id": "PAR-SEC03-004",
  "text": "……",
  "implemented_claim_ids": ["CLM-SEC03-006", "CLM-SEC03-007"],
  "citation_keys": ["ref_p003", "ref_p008"]
}
```

正文装配时：

- 只显示自然语言和引用；
- Claim ID仅保留在结构化产物中；
- 程序验证每个核心Claim是否被实现；
- 程序验证段落中使用的引用是否来自对应Claim。

### 7.3 禁止隐式扩写

需要增加正文与Claim Ledger的差异检查。

对每个正文事实句判断：

```text
是否对应已批准Claim？
```

若没有对应：

- 标记为unplanned_claim；
- 进入强制审计；
- 核心unplanned factual claim阻断发布。

### 7.4 篇幅控制

B2应对每节设置：

- 目标字数；
- 最大段落数；
- 每段最大核心Claim数；
- 每1000字建议事实Claim密度；
- 最大跨论文综合Claim数量。

不应以“正文越长”作为质量提升指标。

推荐先生成6000至9000字的标准综述初稿，而不是默认生成约2万字正文。

---

## 8. 核心修改四：结论章单独设计

### 8.1 为什么需要单独处理

结论章天然包含：

- 跨论文归纳；
- 方法体系总结；
- 研究趋势；
- 语料缺口；
- 未来建议。

这些内容往往不是任何一篇论文的逐字结论，因此不能与普通事实章节使用相同Prompt和合同。

### 8.2 结论章只允许使用以下来源

```text
已在前文章节通过审计的核心Claim
+ 已批准的cross_paper_synthesis
+ corpus_gap
+ 明确标注的normative_recommendation
```

结论章默认不重新读取全部论文并重新形成新的高层结论。

### 8.3 结论Claim类型

```text
direct_summary
multi_paper_synthesis
corpus_gap
normative_recommendation
```

### 8.4 强制语言边界

对于multi_paper_synthesis和corpus_gap，应强制采用类似表达：

- 本次纳入的文献显示；
- 在当前样本文献中；
- 综合所分析研究可以观察到；
- 现有样本主要关注；
- 本次语料尚未充分回答。

禁止：

- 学界一致认为；
- 已形成普遍共识；
- 现有研究已经证明；
- 该领域尚无研究；
- 实践已经表明。

---

## 9. 核心修改五：重构Claim审计分类

### 9.1 当前问题

当前supported、qualified、unsupported三分类不足以区分：

- 真正无来源；
- 来源存在但没有绑定引用；
- 合理综合但强度过高；
- 结果类型写大；
- 验证水平写大；
- 语料范围写大；
- 引用与Claim错配。

### 9.2 建议风险分类

- `supported`：表述、来源、引用和强度均匹配；
- `qualified`：基本有来源，但需要限定条件或降低强度；
- `missing_citation_binding`：来源存在，但正文段落未绑定足够引用；
- `unsupported_inference`：可能是合理综合，但当前来源集合不足；
- `overgeneralized_synthesis`：少量论文被上升为领域共识；
- `result_type_overstatement`：算法、仿真或设计被写成实际效果；
- `validation_level_overstatement`：概念验证被写成全面工程验证；
- `source_mismatch`：引用论文与Claim不匹配；
- `contradicted`：来源明确与正文冲突；
- `unsupported_fact`：没有来源支持的外部事实；
- `unplanned_claim`：正文新增、Ledger中不存在的核心事实或综合判断。

### 9.3 发布门禁

#### 强制阻断

- contradicted；
- unsupported_fact；
- 核心source_mismatch；
- 核心unplanned_claim；
- 未修复的result type写大；
- 未修复的validation level写大。

#### 可通过降格修复

- qualified；
- overgeneralized_synthesis；
- unsupported_inference；
- missing_citation_binding；
- corpus范围写大。

#### 人工确认项

- 高影响cross_paper_synthesis；
- 工程实施状态；
- 政策和工程建议；
- 高风险数字；
- 研究空白判断。

---

## 10. 审计后的人工修订流程

当前阶段不建议自动反复改写整章。

### 10.1 修订动作

每个风险Claim提供：

- 补引；
- 降格；
- 删除；
- 拆句；
- 改为样本内综合；
- 改为作者观点；
- 改为规范性建议；
- 保留并人工确认。

### 10.2 模型只生成建议

模型可以针对风险Claim给出最多三种修订建议：

1. 最小修改；
2. 保守改写；
3. 删除或拆分。

模型不得自动提交最终变更。

### 10.3 程序应用变更

人工选择后：

- 程序保留原文；
- 生成差异；
- 记录修订原因；
- 绑定风险Claim；
- 创建新revision ID；
- 重新执行Claim审计。

---

## 11. A/B对照评价重新设计

### 11.1 评价目的

新的实验不是比较哪个自然输出更长，而是比较：

> 在相同任务要求下，认知规划是否能提高结构和综合质量，同时保持可接受的事实可靠性和人工修改成本。

### 11.2 公平对照条件

A和B2必须使用：

- 同一论文集合；
- 同一综述题目；
- 同一目标字数区间；
- 同一章节数量；
- 同一最大输出Token；
- 同一引用格式；
- 同一Claim审计器；
- 同一模型版本和参数；
- 同一语料范围声明。

建议目标正文统一为：

```text
7000至9000个中文字符
```

### 11.3 方案定义

#### A2：直接全文写作基线

```text
完整Markdown
+ 题录
+ 统一Framework
→ 写作
→ Claim审计
```

A2应使用与B2相同的章节结构，但不提供Paper Understanding、Landscape和Claim Ledger。

#### B2：认知规划与Claim约束写作

```text
Paper Understanding
+ Research Landscape
+ Framework
+ 精简知识包
+ Claim Ledger
→ 分章写作
→ Claim审计
```

#### C：Evidence-only

只作为历史基线，不必继续投入大量优化。

### 11.4 评价指标

#### 可靠性

- 事实Claim支持率；
- 核心事实支持率；
- 每千字unsupported_fact数量；
- 每千字source_mismatch数量；
- result type写大数量；
- validation level写大数量；
- unplanned Claim数量；
- 引用论文匹配率。

#### 综合质量

- 跨论文比较段落比例；
- 有效比较任务完成率；
- 研究分歧呈现；
- 方法—数据—结果—限制链完整度；
- 逐篇复述比例；
- 章节问题回答程度；
- 语料边界表达准确度。

#### 信息价值

- 每个核心研究问题的有效回答数量；
- 每千字有来源的实质性Claim数量；
- 删除风险Claim后剩余正文长度；
- 可保留正文比例。

#### 使用成本

- 总Token；
- 请求数量；
- API费用；
- 人工编辑分钟数；
- 人工修改字符比例；
- 人工修改Claim数量。

### 11.5 组合门槛

不建议继续只使用：

```text
B核心事实支持率 >= A
```

更合理的组合门槛是：

```text
B2核心事实支持率不显著低于A2
且
B2结构和比较质量显著高于A2
且
B2人工修改成本可接受
且
B2每千字严重风险不高于阈值
```

实验性阈值可以暂定为：

- 核心事实支持率不低于A2超过5个百分点；
- 核心unsupported_fact为0；
- 核心source_mismatch为0；
- 结构评分至少4/5；
- 比较评分至少4/5；
- 人工修改字符比例不超过20%；
- Token成本不超过A2的3倍。

这些阈值应标记为试验值。

---

## 12. Paper Understanding的轻量化调整

Paper Understanding已经验证有效，不建议删除，但应避免“越详细越好”。

### 12.1 必保留字段

- research_question；
- study_object；
- data_source；
- method；
- main_contribution；
- result_type；
- validation_level；
- evidence_strength；
- limitations；
- suitable_review_use；
- source bindings。

### 12.2 可减少或延迟生成的字段

- 过长的自然语言分析；
- 与当前综述主题无关的全部论文内容；
- 单篇阶段无法可靠判断的跨论文关系；
- 大量重复关键词；
- 与Card重复的原文摘录。

### 12.3 主题依赖

Paper Understanding应区分：

```text
paper_global_profile
论文相对稳定的全局理解

topic_specific_profile
论文对于当前综述主题的用途和边界
```

全局字段可以缓存复用，主题相关字段在新综述任务中重新生成或更新。

---

## 13. Research Landscape和Framework的修改方向

### 13.1 Landscape关系类型

应明确区分：

- paper-reported relation；
- model-inferred complementarity；
- model-inferred difference；
- explicit contradiction；
- temporal coexistence；
- corpus gap。

不能把“共同构成”或“形成演化”等模型综合关系当成论文原始事实。

### 13.2 Framework增加Claim预算

每节增加：

- core Claim数量上限；
- supporting Claim数量上限；
- cross-paper synthesis数量上限；
- 必须提供限制信息的Claim；
- 高风险Claim列表；
- 结论强度上限。

### 13.3 Framework增加材料选择规则

每节明确：

- 必须纳入的论文；
- 可选论文；
- 仅用于背景的论文；
- 仅用于限制或反例的论文；
- 不应在本节出现的论文。

---

## 14. 程序与数据结构建议

### 14.1 新增模块

```text
claim_ledger_contracts.py
claim_ledger.py
claim_ledger_report.py
llm-claim-ledger CLI
```

职责：

- 生成候选Claim；
- 校验来源；
- 生成确定性ID；
- 输出批准和拒绝清单；
- 记录允许强度和禁止措辞。

### 14.2 修改模块

#### chapter_knowledge_package.py

增加：

- material tier；
- 精选窗口；
- 去重统计；
- 来源覆盖；
- Claim Ledger引用；
- 重复文本比例；
- 上下文组成报告。

#### review_writing_contracts.py

增加：

- implemented_claim_ids；
- unplanned Claim检测；
- Claim到citation key一致性；
- 段落Claim数量限制；
- 核心Claim覆盖率。

#### review_writing.py

改为：

- 只基于Approved Claim Ledger写作；
- 不允许新增核心事实；
- 分章目标长度控制；
- 结论章使用独立Prompt。

#### claim_audit模块

增加新的风险分类和发布门禁。

#### review_framework_contracts.py

增加：

- Claim预算；
- 材料选择策略；
- 高风险比较任务；
- 章节结论强度上限。

### 14.3 建议运行目录

```text
runs/
  claim-ledger/<run_id>/
  chapter-package/<run_id>/
  writing/<run_id>/
  audit/<run_id>/
  revision/<run_id>/
```

每个运行目录继续保存：

- manifest；
- 输入快照；
- 模型配置；
- Prompt哈希；
- 正式输出；
- 原始响应；
- 失败账本；
- Token统计；
- 来源绑定。

---

## 15. 去重与Token治理

### 15.1 上下文去重

知识包生成时统计：

- Markdown与Card文本重复率；
- Card与Evidence重复率；
- Understanding与Landscape语义重复；
- 同一原文窗口在多个对象中的重复出现次数。

默认规则：

- 同一逐字文本只保留一个主副本；
- 其他对象使用ID引用；
- 写作Prompt中不重复展开；
- 审计阶段按需展开。

### 15.2 Token预算

每章继续执行：

```text
输入Token
+ 最大输出Token
+ 安全余量
<= 上下文窗口
```

超预算时执行显式策略：

1. 去除重复材料；
2. 删除低相关窗口；
3. 将完整Markdown降级为精选窗口；
4. 分离背景段和方法段；
5. 缩减非核心Understanding字段；
6. 重新计算；
7. 仍超预算则明确失败。

禁止静默截断、随机删除论文、自动丢弃限制段或不记录地摘要原文。

---

## 16. 人工金标准

下一阶段应建立小规模人工标注集，用于验证Claim审计。

建议样本：

- 20条supported；
- 20条qualified；
- 全部核心unsupported；
- 全部result type写大；
- 全部validation level写大；
- 10条cross-paper synthesis；
- 10条missing citation binding；
- 10条source mismatch候选。

人工标注字段：

```text
claim_type_gold
support_status_gold
source_match_gold
result_type_overstatement_gold
validation_overstatement_gold
scope_overstatement_gold
recommended_action_gold
```

审计百分比只能作为方案间比较指标，不能直接解释为绝对真实准确率。

---

## 17. 模型分层建议

### 可尝试较低成本模型

- Paper Understanding初稿；
- 原文窗口相关性筛选；
- Claim拆句；
- citation key展开；
- 低风险Claim分类；
- 修订建议生成。

### 优先使用高能力模型

- Research Landscape；
- Review Framework；
- cross-paper synthesis Claim规划；
- 章节写作；
- 高风险Claim审计；
- 结论章综合。

必须建立阶段级模型、上下文、输出预算和价格配置。

---

## 18. UI暂缓与最小可用流程

在B2通过实验门槛前，不要把全部内部阶段接入现有UI。

若B2达到基本可用状态，建议UI最小流程为：

```text
1. 选择论文池
2. 设置综述主题和目标
3. 查看并确认Framework
4. 查看核心Claim计划
5. 生成综述草稿
6. 查看风险Claim
7. 逐条选择修订动作
8. 重新审计
9. 导出正文和审计报告
```

普通用户不应直接面对大量run ID、完整Schema和内部机器ID。

---

## 19. 分阶段实施计划

### Phase 0：冻结现有B1和实验结果

- 保留当前已接受运行；
- 不覆盖B1结果；
- 标记B1为historical baseline；
- 保留A/B/C评价报告；
- 新建独立分支开发B2。

### Phase 1：知识包去重和精简

任务：

1. 分析当前知识包重复率；
2. 实现Selected Source Windows；
3. 实现material tier；
4. 移除默认全部Card展开；
5. 移除默认全部Evidence展开；
6. 保留按需回查；
7. 输出Token和去重报告。

验收：

- 同章节Token明显下降；
- 所有引用仍可回查；
- 关键方法、结果和限制上下文未丢失；
- 不通过静默截断降低Token。

### Phase 2：实现Claim Ledger

任务：

1. 定义Claim类型和Schema；
2. 生成候选Claim；
3. 确定性生成Claim ID；
4. 校验支持论文和来源窗口；
5. 生成允许强度和限定语；
6. 输出批准和拒绝报告。

验收：

- 所有核心Claim存在来源绑定；
- cross-paper synthesis至少绑定两篇论文；
- corpus gap均带语料范围；
- 不合格Claim不能进入写作。

### Phase 3：受约束章节写作

任务：

1. 修改写作Prompt；
2. 每段输出implemented_claim_ids；
3. 检查citation key与Claim一致；
4. 检测unplanned Claim；
5. 增加篇幅和Claim密度控制；
6. 为结论章建立独立合同。

验收：

- 核心Claim覆盖率100%或明确报告未覆盖；
- 核心unplanned factual claim为0；
- 正文机器ID为0；
- 引用均能映射到Claim Ledger。

### Phase 4：重构Claim审计

任务：

1. 扩展风险分类；
2. 区分事实错误和缺引用；
3. 区分合理综合和过度概括；
4. 更新发布门禁；
5. 输出逐Claim建议动作；
6. 建立人工金标准样本。

### Phase 5：修订工作流

任务：

1. 生成修订建议；
2. 人工选择动作；
3. 保存diff；
4. 创建新revision ID；
5. 重新审计；
6. 防止迭代漂移。

### Phase 6：A2/B2公平对照

任务：

1. 固定章节和目标字数；
2. 使用同一模型和审计器；
3. 生成A2和B2；
4. 进行自动评价；
5. 完成实际人工编辑；
6. 记录editing_minutes和edited_char_ratio。

### Phase 7：决定产品化范围

只有B2通过约定门槛后，才决定：

- 是否成为默认写作方案；
- 是否作为高级模式；
- 哪些模块接入UI；
- 是否保留直接全文写作作为快速模式；
- 是否继续扩展到几十篇以上语料。

---

## 20. B2验收标准建议

B2不要求自动生成可直接发表的综述。

目标定义为：

> 生成结构清晰、跨论文综合充分、主要事实可审计、人工修改成本可控的高质量综述初稿。

### 数据与运行

- 所有阶段有显式run ID；
- 上游运行显式绑定；
- 输入哈希和Prompt哈希可重放；
- 失败不覆盖旧结果；
- 不自动补造ID；
- 不静默截断。

### Claim规划

- 核心Claim来源绑定率100%；
- 核心cross-paper synthesis至少绑定两篇论文；
- corpus gap范围限定率100%；
- 核心Claim允许强度字段完整。

### 写作

- 核心Claim实现率不低于95%；
- 核心unplanned factual claim为0；
- 机器ID为0；
- 引用与Claim映射完整；
- 逐篇复述比例低于预设阈值。

### 审计

- 核心unsupported_fact为0；
- 核心contradicted为0；
- 核心source_mismatch为0；
- result type写大全部修复；
- validation level写大全部修复；
- 样本范围越界全部修复。

### 质量与成本

- 结构评分至少4/5；
- 比较评分至少4/5；
- 研究问题回答程度至少4/5；
- 人工修改字符比例处于可接受范围；
- Token成本明显低于B1；
- 建议目标不超过A2的3倍。

---

## 21. 开发原则

下一阶段必须遵守：

1. 不删除已经验证有效的Paper Understanding、Landscape和Framework；
2. 不恢复Evidence-only写作；
3. 不把完整Markdown直接写作当成唯一长期方案；
4. 不继续通过增加上下文解决所有问题；
5. 不认为1M上下文可以替代信息组织；
6. 不让模型同时承担语义判断和精确数据搬运；
7. 能由程序校验的内容不用Prompt兜底；
8. 不将审计无法证明直接等同于内容必然错误；
9. 不将模型综合判断伪装成论文原话；
10. 不将定向语料缺口写成领域空白；
11. 不默认自动修订并覆盖正文；
12. 不在B2通过门槛前接入默认Pipeline和UI；
13. 不以生成篇幅作为主要成功指标；
14. 每项架构修改必须通过真实14篇论文重新验证。

---

## 22. Codex下一步工作要求

请基于现有代码和本指南开展下一步工作。

不要立即重写全部Pipeline。

第一步先输出一份B2实施分析，至少包括：

1. 当前B1代码和数据流映射；
2. 当前章节知识包的重复来源分析；
3. Claim Ledger插入点；
4. 需要新增和修改的Schema；
5. 需要新增和修改的模块；
6. 对现有运行目录和manifest的影响；
7. 与历史B1运行的兼容策略；
8. 分阶段实现计划；
9. 每阶段测试方案；
10. 每阶段验收标准；
11. 预计Token变化；
12. 主要风险和回滚方式。

在分析通过后，再按照以下顺序开发：

```text
知识包精简
→ Claim Ledger
→ 受约束写作
→ 结论章合同
→ 审计分类
→ 人工修订流程
→ A2/B2公平评价
```

不得跳过中间阶段直接改UI。

不得以修改旧输出、人工补造JSON、删除失败字段或放宽Schema的方式使运行通过。

---

## 23. 最终方向

项目下一阶段不是在以下两个极端中选择：

```text
极端一：完整Markdown直接写作
极端二：Evidence-only写作
```

而是形成一条新的中间路线：

```text
认知层负责理解和规划
原文层负责提供语境
Claim Ledger负责限制可写内容
写作层负责形成自然论述
Evidence与原文负责证明
审计层负责发现和分类风险
人工负责关键修订决策
```

B2的目标不是自动替代研究者，而是尽可能把研究者真正耗时的工作结构化：

- 论文理解；
- 主题组织；
- 章节规划；
- 跨论文比较；
- 引用定位；
- 风险检查；
- 修订决策支持。

最终应形成一个：

> 能生成较高质量综述初稿，同时保留研究认知、来源证据和人工控制权的AI辅助文献综述系统。
