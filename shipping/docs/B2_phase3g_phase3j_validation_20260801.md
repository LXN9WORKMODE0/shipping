# B2 Phase 3G-3J 独立结论与最终装配验证记录

日期：2026-08-01

## 1. 本阶段目标

本阶段承接已经通过正文门禁的八个前文章节，完成以下闭环：

```text
受审计正文
→ 结论专用Claim目录
→ 独立结论生成
→ 逐句结论语义审计
→ 显式修订决策
→ 修订结论复审
→ 最终综述与参考文献装配
```

结论阶段不重新读取论文Markdown、Card、Evidence或Source Window。模型只能使用前文章节已经批准且通过审计的核心Claim，以及已批准的`cross_paper_synthesis`、`corpus_gap`和`normative_recommendation`。

## 2. 实现内容

### 2.1 Phase 3G：结论专用合同

新增：

- `review_conclusion_b2_contracts.py`
- `review_conclusion_b2.py`
- `review_conclusion_b2_report.py`
- `config/review-conclusion-b2-default.json`
- CLI：`llm-review-conclusion-b2`

真实输入包含44条合格Claim：

- `direct_fact`：13
- `comparative_fact`：13
- `author_view_summary`：1
- `cross_paper_synthesis`：6
- `corpus_gap`：6
- `normative_recommendation`：5

结论固定为四段：总体发现、路径比较、样本文献局限、未来方向。程序校验来源Claim、引用并集、八章覆盖、样本文献限定语、禁止表述、篇幅和机器ID。

### 2.2 Phase 3H：结论专用语义审计

新增：

- `review_conclusion_audit_b2_contracts.py`
- `review_conclusion_audit_b2.py`
- CLI：`llm-review-conclusion-audit-b2`

审计单位是结论句相对于已批准来源Claim的语义边界。程序额外检测某句是否依赖本段未绑定的Claim，并将其确定性记为`source_mismatch`。

### 2.3 Phase 3I：显式结论修订

新增：

- `review_conclusion_b2_revision.py`
- `config/review-conclusion-revision-b2-decision-20260801.json`
- CLI：`apply-review-conclusion-revision-b2`

修订决策只能删除或替换稳定句子ID，替换句必须显式列出保留的原段Claim。程序根据复审结果重新计算每段Claim、引用和八章实际覆盖，不继承原生成阶段的挂名来源。

### 2.4 Phase 3J：最终装配

新增：

- `review_b2_final_assembly.py`
- `config/review-b2-final-release-20260801.json`
- CLI：`assemble-review-b2-final`

最终装配同时验证：

- 八章正文来自同一受审计发布集合；
- 结论输入指向该受审计正文；
- 结论复审指向当前修订结论；
- 正文和结论均无阻断项；
- 所有正文引用均可映射到冻结题录。

## 3. 真实运行与暴露问题

### 3.1 首次结论生成

运行：`conclusion-b2-14papers-20260801-v1`

- 状态：完成
- 输入：17228 tokens
- 输出：1998 tokens
- 隐藏推理字符：0
- `finish_reason`：`stop`
- 结论正文：1061字符
- 使用来源Claim：32

程序结构合同通过，但人工审视发现“新建通道渐成共识”“数据实际上限缩了挖潜空间”“协同效应与交互机制尚无系统考察”等连接性表达需要独立审计，不能直接发布。

### 3.2 两次失败审计

保留以下失败运行：

- `conclusion-audit-b2-14papers-20260801-v1`
- `conclusion-audit-b2-14papers-20260801-v2`

失败原因不是网络或输出截断，而是审计模型选择了当前段落之外的Claim ID。该问题揭示：同一个citation key下可能存在多个Claim，引用正确不等于Claim绑定正确。

审计合同随后改为允许模型如实指出任意已使用Claim，再由程序确定性判定段外依赖，避免以Schema失败掩盖来源错绑。

### 3.3 可执行结论审计

运行：`conclusion-audit-b2-14papers-20260801-v3`

- 句子：12
- 阻断：4
- 限定：1
- `unsupported_inference`：2
- `source_mismatch`：2
- 状态：需要修订

最小修订包括：

- 删除2个由分散缺口推导出的新增综合句；
- 删除1个完全依赖其他段落Claim的句子；
- 收紧2个比较句；
- 补写1个第1节实际通航量背景句。

### 3.4 首次修订失败

运行：`conclusion-revision-b2-14papers-20260801-v1`

状态：失败。

程序根据句子审计重新计算实际使用Claim后发现第1节没有任何内容真正进入结论，原结论的八章覆盖只是来源ID挂名。该运行以`conclusion_b2.section_coverage_incomplete`失败，没有放宽门禁。

决策文件随后增加第1节通航量背景的实际表达。

### 3.5 修订与复审通过

修订运行：`conclusion-revision-b2-14papers-20260801-v2`

- 正文：987字符
- 实际覆盖章节：1至8
- 状态：完成，等待复审

复审运行：`conclusion-audit-b2-revision-14papers-20260801-v1`

- 句子：9
- `supported`：9
- 阻断：0
- 限定：0
- 来源错配：0
- 状态：通过

## 4. 最终结果

最终运行：`review-b2-final-14papers-20260801-v1`

- 综述ID：`review_b2_final_542c89216a8883dc3f9b`
- 章节：9个正文/结论章节
- 实际引用参考文献：13条
- Markdown字符：约9523
- 正文机器ID：0
- 正文阻断：0
- 正文非阻断残余：1个既有`qualified`
- 结论阻断：0
- 发布门禁：通过

主要文件：

- 成稿：`workspace/_review_final_b2/runs/review-b2-final-14papers-20260801-v1/review/review_b2_final.md`
- 发布审计：`workspace/_review_final_b2/runs/review-b2-final-14papers-20260801-v1/review/release_audit.md`
- 结构化输出：`workspace/_review_final_b2/runs/review-b2-final-14papers-20260801-v1/output/review_b2_final.json`

14篇纳入论文中有1篇没有进入最终正文引用，因此参考文献表只输出13条实际引用题录，不为未使用论文制造引用。

## 5. 阶段结论

独立结论合同已经完成并通过一次真实语料闭环。当前结果证明：

1. 结论不需要重新吞入全文，也能基于已审计Claim形成完整总结；
2. 仅校验citation key不足以证明Claim级来源绑定正确；
3. 八章覆盖必须根据句子实际支持Claim重算，不能沿用生成模型声明；
4. 失败运行保留后，问题可以在不同阶段被准确定位，不需要通过放宽Schema或覆盖旧结果来收束；
5. 当前B2成稿已经达到可供质量评价的候选状态，但“门禁通过”不等同于写作质量优于直接全文写作。

## 6. 下一步

下一阶段应执行A2/B2公平质量评价：使用同一14篇论文、同一综述主题和相近篇幅，对当前B2成稿与直接全文写作基线进行盲化评价。重点比较主题回答度、综合质量、事实与引用可靠性、可读性和人工修改成本，而不是继续细化当前结论合同。
