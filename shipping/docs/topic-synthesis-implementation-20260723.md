# 跨论文主题综合实施记录

日期：2026-07-23，2026-07-24 审查增强与 v2 合同升级

## 1. 本轮目标

实现从多篇 `llm-topic-brief` 不可变运行到跨论文主题矩阵、综合单元和段落级综述提纲的完整功能。由于当前没有经过统一批处理的大规模论文简报语料，本轮只验证功能、契约、状态隔离和现有少量真实来源的可读取性，不把输出质量标记为大规模验收通过。

## 2. 已实现流程

新增命令 `llm-topic-synthesis`：

```text
显式 collection
  -> 重验单篇 v2 manifest、paper brief、Evidence 和冻结 Card
  -> 冻结来源文件哈希及模型投影
  -> theme_map 全局调用
  -> Evidence 完整性和论文关系校验
  -> outline 全局调用
  -> 主题、综合单元、章节和 Evidence 引用校验
  -> coverage 审计
  -> 发布 JSON 与中文 Markdown 报告
```

只接受 `llm.topic_review_run.v2`、`llm.topic_paper_result.v2` 和 `llm.topic_review_config.v2`。collection 中同一论文只能出现一次，所有来源必须与 collection 主题完全一致。程序根据冻结 `materials.jsonl` 重新生成引文候选，并重放 Evidence 的逐字引文、数字、限定词和表格规则；来源文件哈希均进入综合输入身份。`completed_with_revisit_failure` 的基础简报允许进入，但部分状态会进入来源清单和覆盖报告；旧 v1、失败、排除、缺失文件和 Evidence 引用损坏直接失败。

## 3. 关键合同

### 主题矩阵

- 每条来源 Evidence 必须且只能进入一个主主题，或者进入显式未归组集合。
- 一个独立论文来源的主题由程序生成 `single_source`。
- 其他主题关系必须至少涉及两篇独立论文。
- Evidence 条数不视为独立研究数量。
- 主题 ID 由代码根据主题、标题和关注问题生成。
- 允许只有一个真实主题；不强制拆出第二个主题来满足形式数量。
- 每个语料缺口由代码生成稳定 ID，重复缺口直接拒绝。

### 综合提纲

- 一个独立论文来源的非缺口综合单元由程序生成 `single_source_context`。
- 其他非缺口综合单元必须引用至少两篇论文。
- 综合单元 Evidence 必须来自其声明主题。
- 每个主题必须至少进入一个综合单元。
- 未入纲的额外综合单元完整保留并进入覆盖审计；每个主题仍必须至少进入一个章节。
- 章节主题由段落引用的综合单元求并集，同一主题可进入综合讨论或缺口章节。
- 段落 Evidence 由引用的事实综合单元求并集，不由模型重复填写。
- 段落角色和 Evidence/gap 绑定由引用单元生成；事实与缺口可以在同一限制性段落中并列，但两条来源链分别保留。
- 缺口段落不得伪造 Evidence。
- `corpus_gap` 综合单元必须引用已识别的缺口 ID，非缺口单元不得引用缺口。
- 检索方向只选择值得继续追查的缺口；未优先缺口完整保留并进入覆盖审计。
- 模型临时序号验证后转换为稳定综合单元 ID。

数字不再用字符正则阻断。导航字段不能替代 Evidence；事实追溯仍以 Evidence claim 和逐字原文为准。

v2 的职责调整、保留门禁和四次真实历史响应回放见 `docs/topic-synthesis-contract-v2-20260724.md`。程序派生过程写入 `audit/contract_derivations.json`。

## 4. 状态和产物

运行状态只有：

- `completed`：两次模型调用和全部合同均通过。
- `failed`：任一输入、调用、Token 或合同失败。

主题矩阵成功而提纲失败时，保留中间请求和 `validated_theme_map.json`，但不发布 `output/topic_synthesis.json`。运行先写 `running` manifest，只有 JSON、中文报告和全部校验共同完成后才写 `completed`；发布阶段失败会清除两份正式产物。正式结果、报告和覆盖账本分别位于：

```text
workspace/_topic_syntheses/runs/<run_id>/
  output/topic_synthesis.json
  review/topic_synthesis.md
  audit/coverage.json
```

中文报告直接展示论文标题、Evidence claim、逐字原文、Card 标题和 Markdown 行号。Evidence ID 保留为机器锚点，但人工阅读不依赖跨文件查找。

## 5. Token 与模型调用

v1 固定执行两次全局调用，不按字符拆批：

1. `topic_synthesis_theme_map`
2. `topic_synthesis_outline`

主题矩阵输出预算按 Evidence 数计算，提纲输出预算按主题数计算；两者都受 model profile 的上下文窗口、模型最大输出和 safety margin 约束。超限错误有显式代码并写入该阶段 `result.json`，不自动缩 Prompt、换模型或切批。

## 6. 验证

自动测试：

- 跨论文专项测试：17项通过。
- 全工程测试：247项通过。
- Python 编译检查：通过。
- Python 编译检查：通过。

模拟模型测试覆盖：

- 两阶段完整成功并发布。
- 主题矩阵完整覆盖。
- 单论文伪装跨论文关系被拒绝。
- Evidence 与主题错绑被拒绝。
- 综合单元与章节主题错绑被拒绝。
- 提纲失败不发布半成品。
- 发布写入失败会清除已经写出的另一份正式产物。
- 预算预检失败会保留结构化阶段结果。
- 来源 Evidence 缺少或篡改引文时，冻结 Card 重放校验会拒绝。
- 主题、综合单元、段落和语料缺口的覆盖链均有反例测试。
- 部分状态单篇来源被显式带入覆盖报告。
- 重复论文和旧 v1 来源被拒绝。

只读真实来源检查使用 `config/pilots/topic-synthesis-smoke-20260723.json`：

- 来源论文：4篇。
- 严格 Evidence：17条。
- 主题矩阵 Prompt：3966 Token。
- 主题矩阵计划最大输出：7360 Token。
- 来源快照 SHA-256：`sha256:ab27ccda90c97c8afe3879341b1f3cad35a9dee412cd79955ab09c5858fe6307`。

这次检查没有调用 LLM API，没有生成真实主题矩阵和提纲。

## 7. 当前判断

跨论文综合的输入、调用、合同、状态、审计和报告链路已经实现，可以纳入后续整套 pipeline 联调。

尚未证明：

- 大规模论文下的主题稳定性。
- 多学科或高度冲突语料中的关系判断质量。
- 正式综述正文的事实句约束。
- 作者、年份、DOI 和参考文献格式生成。

后续一次性跑通整套流程时，应先批量生成统一 v2 单篇简报，再用本命令完成真实跨论文综合验收。当前不为有限语料增加分批、兼容旧版或自动参考文献分支。
