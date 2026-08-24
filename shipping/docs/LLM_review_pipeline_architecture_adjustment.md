# 文献综述自动化 Pipeline 架构调整任务

## 一、背景说明

当前项目已经完成：

- PDF → Markdown解析；
- Markdown → Card结构化拆分；
- 单篇论文分析；
- Evidence抽取；
- Evidence溯源；
- 跨论文主题聚合；
- 综述提纲生成；
- 正文生成；
- 引用和审计链。

当前系统的问题已经明确：

不是PDF解析能力不足，也不是Evidence抽取能力不足，而是在“论文理解 → 综述框架形成 → 正文生成”之间缺少一个中间认知层。

现有流程：

```
论文全文
↓
Card
↓
Evidence
↓
正文生成
```

存在结构性问题：

Evidence适合作为事实验证和引用锚点，但不适合作为LLM生成综述的唯一输入。

原因：

1. Evidence只保存论文中的局部结论；
2. 丢失论文的问题背景；
3. 丢失研究方法和结果之间的逻辑关系；
4. 丢失作者贡献边界；
5. 无法体现不同论文之间的发展关系；
6. 容易导致LLM将局部结果外推为领域共识。

因此，需要对系统进行架构调整。

---

# 二、核心调整目标

不要继续强化：

```
论文 → Evidence → 综述
```

而应该建立：

```
论文 → 领域认知 → 综述框架 → 写作 → Evidence审计
```

的新流程。

目标不是减少LLM阅读论文。

相反：

LLM需要大量阅读论文。

但是阅读后的产物不应该直接是Evidence，而应该首先形成：

## Research Understanding Layer（论文认知层）

该层用于模拟研究者阅读大量论文后的领域理解。

---

# 三、新增核心模块：Research Understanding Layer

请设计新增的数据结构和处理流程。

目标：

让系统能够回答：

对于某一个综述主题：

1. 这个领域主要研究问题有哪些？
2. 不同论文分别解决什么问题？
3. 不同研究方法属于什么类别？
4. 哪些论文之间存在继承、补充或冲突关系？
5. 哪些结果属于模型模拟？
6. 哪些属于实际工程验证？
7. 哪些只是作者提出的建议？
8. 当前领域真正存在的研究空白是什么？

---

# 四、新增对象设计建议

新增：

## Paper Understanding Card

区别于现有Evidence。

Evidence回答：

> 论文说了什么？

Paper Understanding回答：

> 这篇论文在领域中意味着什么？

建议包含：

```json
{
  "paper_id": "",
  "research_question": "",
  "problem_category": "",
  "method_category": "",
  "data_source": "",
  "study_object": "",
  "main_contribution": "",
  "result_type": "",
  "evidence_strength": "",
  "validation_level": "",
  "limitations": "",
  "relation_to_other_papers": "",
  "suitable_review_section": "",
  "keywords": []
}
```

注意：

该对象不是替代Evidence。

二者关系：

- Paper Understanding：负责理解；
- Evidence：负责证明。

---

# 五、新增阶段：Research Landscape Generation

在正式生成综述之前，增加一个阶段：

输入：

- 全部Paper Understanding Card；
- 综述主题；
- 用户目标。

输出：

## Research Landscape

例如：

主题：

三峡枢纽通航能力提升方法

研究维度：

```
1. 运行组织优化
   - 调度模型
   - 闸室利用率优化

2. 工程扩能方法
   - 新建通道
   - 船闸改造

3. 船舶大型化
   - 船型标准
   - 运输组织

4. 智能化发展
   - 数据平台
   - AI优化
```

该阶段作用：

不是写文章。

而是让LLM建立领域地图。

---

# 六、综述框架生成调整

当前：

```
Evidence → 提纲
```

调整为：

```
Research Landscape → 综述框架
```

流程：

```
Research Landscape

+

用户综述目的

+

论文质量评价

↓

生成章节结构
```

生成结果应该包括：

- 一级章节；
- 二级章节；
- 每章节回答的问题；
- 使用哪些论文；
- 需要讨论哪些争议；
- 存在哪些不足。

---

# 七、正文生成调整

禁止：

一次性使用全部论文生成全文。

改为：

章节级生成。

流程：

```
章节任务

+

相关Paper Understanding

+

相关论文Markdown/Card

+

Evidence

↓

章节正文
```

其中：

Paper Understanding负责提供：

- 思考框架；
- 论文关系；
- 方法比较。

Evidence负责：

- 引用；
- 数字；
- 事实边界检查。

---

# 八、保留现有模块

不要删除：

1. Card系统；
2. Evidence系统；
3. 引用审计系统；
4. 论文池系统。

它们重新定位：

| 模块 | 新定位 |
|---|---|
| Card | 论文检索和定位 |
| Evidence | 事实审计和引用锚点 |
| Research Understanding | 领域认知 |
| Writing | 语言生成 |

---

# 九、下一步开发任务优先级

请按照以下优先级规划开发。

## 第一阶段

新增Paper Understanding Card。

要求：

- 基于现有Card和论文Markdown生成；
- 保留论文来源；
- 可追踪；
- 支持重新生成。

---

## 第二阶段

新增Research Landscape生成模块。

要求：

输入论文集合和主题。

输出：

- 研究主题；
- 分类体系；
- 论文关系；
- 研究演化；
- 研究空白。

---

## 第三阶段

修改正文生成Pipeline。

从：

```
Evidence-only
```

改为：

```
Chapter-level Knowledge Package
```

---

## 第四阶段

建立评价体系。

评价：

1. 是否覆盖论文；
2. 是否存在事实越界；
3. 是否正确区分：
   - 模型结果；
   - 实验结果；
   - 工程实施；
   - 作者观点；
4. 是否体现论文之间关系；
5. 是否减少人工修改成本。

---

# 十、开发原则

不要继续优化：

- Card数量；
- Evidence数量；
- 更严格的Evidence压缩。

当前瓶颈不是信息不足。

而是：

信息经过压缩后失去了科研语境。

新的目标：

让系统像研究者一样：

1. 先阅读大量论文；
2. 形成领域认知；
3. 规划综述结构；
4. 再写文章；
5. 最后用Evidence检查。

请先分析现有代码结构，定位新增模块应该插入的位置，并提出详细修改方案。

不要直接重构全部代码。

第一步输出应该包括：

1. 当前代码架构分析；
2. 数据流分析；
3. 新模块插入点；
4. 需要新增的数据结构；
5. 对现有模块的影响评估；
6. 分阶段实施计划。

