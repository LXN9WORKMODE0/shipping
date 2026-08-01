# B2 Phase 3E/3F 修订应用与受审计装配记录（2026-08-01）

## 1. 目标

承接 Phase 3C/3D 的独立裁决和修订建议，本轮完成：

1. 将用户确认的修订选择固化为显式决策文件；
2. 不覆盖原写作运行，确定性生成新章节 revision；
3. 对 revision 重新执行完整逐句 Claim 审计；
4. 在审计模型波动下建立有限重试和终止规则；
5. 将 generation 与 revision 混合章节装配为受审计前文草稿。

## 2. Phase 3E 实现

新增：

- `review_writing_b2_revision_contracts.py`
- `review_writing_b2_revision.py`
- `review_writing_b2_revision_report.py`
- `tests/test_review_writing_b2_revision.py`
- `config/review-writing-revision-b2-decision-20260801.json`
- `config/review-writing-revision-b2-second-pass-decision-20260801.json`

新增 CLI：

```powershell
python main.py apply-review-writing-revision-b2 `
  --revision-suggestion-run-id <suggestion-run-id> `
  --decision-file <decision-file> `
  --run-id <writing-revision-run-id>
```

关键约束：

- 决策必须按顺序覆盖当前建议运行中的全部风险句；
- option ID 必须属于对应风险句；
- 用户明确删除和裁决后明确删除分别记录为 `user_confirmed_delete`、`adjudication_confirmed_delete`；
- 原段落必须可由冻结句子目录无损重建，否则拒绝应用；
- 删除后段落不得为空；
- 所有替换句必须具有句末标点；
- 旧章节、旧建议和失败运行不覆盖；
- 新章节重新计算 chapter ID、paragraph ID，并保存逐句 diff；
- revision loader 会从建议运行、决策集和原章节确定性重放输出；
- 原审计器改用显式写作来源分派，可同时读取 generation 和 revision，若同一 run ID 在两类根目录中重复则拒绝。

## 3. 首轮决策映射问题

用户确认的目标是“删除 8 句、改写 2 句”。首次应用时发现：

- `delete_or_split` 是候选类型，不代表实际 operation 一定是 `delete`；
- 第 1、6、8 节各有一个该类型候选实际为 `split`；
- 仅根据候选类型挑 option ID 会把明确删除错误映射为拆句。

六个 `writing-revision-b2-14papers-section-*-20260801-v1` 运行保留为错误映射的历史结果，未进入后续审计门禁。

修正：决策合同增加显式 `user_confirmed_delete`，重新生成六个 v2 运行。v2 汇总严格为：

- 删除：8；
- 替换：2；
- 拆句：0；
- 共同 decision set ID：`review_writing_revision_decisions_b2_4ea8c9457f42805448b8`。

## 4. 首轮 revision 重新审计

六章完整重审结果：

- 第 5、8 节直接通过；
- 第 1、2、4、6 节初审共出现 7 个风险句；
- 独立裁决后驳回 3 个，保留 4 个。

保留的 4 个问题：

1. 第 1 节：否定调度优化和局部工程改造能够根本解决缺口，属于计划外综合；
2. 第 2 节：3 月岁修期间 709 艘次和 100 小时数据来源存在，但未纳入计划 Claim；
3. 第 4 节：对算法与过闸组织方式作“实施程度、验证规模低于”的等级判断；
4. 第 6 节：措施与利用率数据后追加“信息化与管理优化协同改善效率”的解释性判断。

## 5. 第二轮最小处理

第二轮只处理上述 4 句，不扩写正文：

- 第 1、4 节删除整句；
- 第 2 节删除未计划的 3 月岁修数据，仅保留平均在锚时间和同比变化；
- 第 6 节删除句末解释性判断，仅保留措施和闸室利用率数据。

第二轮决策依据显式记录为 `adjudication_exact_removal`，没有伪写成新的用户确认。

第二轮完整重审：

- 第 1、4 节直接通过；
- 第 2 节一个 `unsupported_fact` 经独立裁决后驳回；
- 第 6 节一个 `qualified` 经独立裁决后确认，但无阻断性。

第 6 节残余句为：

> 随着三峡船闸日趋饱和，管理挖潜措施持续推进。

其问题是“持续推进”比已批准 Claim 中“已采取措施”的表述更宽。该句为低影响过渡句；前一次独立裁决曾判其得到支持，说明模型存在边界波动。为避免无限循环删改，终止规则为：

- 强制阻断风险必须清零；
- 经独立裁决确认的 `qualified` 可作为显式非阻断残余记录；
- 不再启动第三轮整章改写。

## 6. Phase 3F 受审计装配

新增：

- `review_writing_b2_audited_assembly.py`
- `tests/test_review_writing_b2_audited_assembly.py`
- `config/review-writing-b2-audited-release-20260801.json`

新增 CLI：

```powershell
python main.py assemble-review-b2-audited `
  --release-file config/review-writing-b2-audited-release-20260801.json `
  --run-id review-b2-audited-14papers-20260801-v1
```

最终章节来源：

| 节 | 章节来源 | 门禁依据 |
|---|---|---|
| 1 | 第二轮 revision | audit passed |
| 2 | 第二轮 revision | adjudication passed |
| 3 | 原写作 v7 | audit passed |
| 4 | 第二轮 revision | audit passed |
| 5 | 第一轮 revision v2 | audit passed |
| 6 | 第二轮 revision | accepted nonblocking qualified |
| 7 | 原写作 v6 | adjudication passed |
| 8 | 第一轮 revision v2 | audit passed |

装配器逐章验证：

- 章节 run、chapter ID 与 audit 来源链一致；
- adjudication 必须引用选定 audit run；
- `audit_passed` 和 `adjudication_passed` 必须由正式 summary 证明；
- `accepted_nonblocking` 必须满足阻断数为 0，且残余风险属于显式允许集合；
- 八章必须来自同一 Framework，section ID 和顺序必须一致；
- 引用元数据不得冲突。

## 7. 最终结果

受审计装配运行：

- run：`review-b2-audited-14papers-20260801-v1`
- draft ID：`review_draft_b2_audited_b8611a1b46181012798d`
- 章节数：8
- 阻断句：0
- 非阻断残余：1 个 `qualified`
- 状态：`passed_with_nonblocking_residuals`
- `body_release_ready=true`
- `conclusion_input_ready=true`
- `formal_review_published=false`

人工可读正文：

`workspace/_review_drafts_b2_audited/runs/review-b2-audited-14papers-20260801-v1/review/review_draft_b2_audited.md`

当前前文已经可作为独立结论章的输入，但结论尚未生成，正式综述仍未发布。

## 8. 下一步

进入独立结论章合同：

- 只读取受审计章节的核心 Claim、章节结论和显式残余；
- 不重新读取全部论文并形成新的高层结论；
- 结论中的每个综合判断必须绑定前文 audited Claim；
- 单独执行结论 Claim 审计；
- 结论通过后再形成正式装配和质量评价。
