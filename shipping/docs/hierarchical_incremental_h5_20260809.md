# H5 候选晋级与受影响主题增量重算

## 目标

H5承接H4的候选建议，但不把H4的语义匹配直接当作证据。它先为候选生成完整Paper Understanding，再判断论文是否真正回答对应回看请求。只有通过门禁的论文进入局部主题综合。

## 输入

- 一个已完成且可严格重放的H4运行；
- H4所属的全局Landscape、局部Landscape批次和论文池筛选账本；
- H4发布的候选分配及每条全局回看请求的目标主题簇。

H5不接受人工编辑后的独立候选文件。H4输入哈希、稳定ID和来源代际不一致时直接拒绝运行。

## 处理流程

1. 对H4涉及的唯一候选论文运行相互隔离的Paper Understanding。单篇失败只写入不可用账本，不阻断其他候选，也不能晋级。
2. 将每条候选分配与对应完整Paper Understanding提交给候选晋级裁决器。
3. 裁决结果固定为`direct`、`partial`或`not_supported`。只有`direct`且置信度为`high`或`medium`可以晋级。
4. 每个回看请求默认最多晋级4篇。超出上限时按置信度和稳定record_id确定性截取。
5. 晋级论文只加入回看请求原先声明的`target_cluster_ids`，不得由H5扩展到其他簇。
6. 受影响簇使用“原Understanding + 晋级Understanding”创建新collection并重新运行Research Landscape。
7. 未受影响簇严格继承上一局部批次的正式Landscape运行ID，不重新调用模型。
8. 所有受影响簇成功后，才基于新旧局部结果的组合批次重新运行全局Landscape。局部失败时禁止发布新全局结果。

## 状态

- `completed`：至少一篇候选晋级，受影响簇和新全局Landscape均成功。
- `completed_no_changes`：H4没有候选，或完整理解后没有候选通过晋级门禁；旧全局结果保持当前代际。
- `completed_partial`：部分候选Understanding不可用，但其余候选产生了晋级并完成增量重算。
- `completed_partial_no_changes`：部分候选Understanding不可用，已成功裁决的候选没有晋级；不能把它解释为全部候选均不支持请求。
- `failed`：血缘不可重放、裁决合同失败、受影响簇失败或新全局归并失败。

失败运行保留审计文件，但不替换任何旧产物。

新H5代际可通过`--resume-from-run-id`复用父代际已成功的Paper Understanding，只重试失败候选。续跑必须引用同一个H4运行；裁决和后续增量计算在新代际重新执行。

## 输出与审计

- `candidate_adjudications.jsonl`：每条H4候选分配的请求级裁决；
- `promotions.jsonl`：实际晋级论文及目标主题簇；
- `understanding_unavailable.jsonl`：Paper Understanding失败或不可用的候选；
- 新局部批次：同时记录重算簇和复用簇；
- 新全局运行：只在局部批次完整成功后产生；
- 中文审核报告：展示候选数、未裁决数、晋级数、受影响簇和复用簇。

## 当前真实输入预检

`hierarchical-lookback-6papers-20260809-v3`已通过H5只读严格重放：共有6条候选分配、6篇唯一论文，目标全部为`operation-baseline`。按默认配置，后续真实运行最多晋级4篇，只应重算1个局部主题簇并复用另外2个主题簇。

## 真实运行记录

`hierarchical-incremental-6papers-20260809-v2`完成首轮处理：6篇候选中3篇Paper Understanding成功，3篇因模型输出不符合Understanding Schema而失败。成功的3篇分别被裁决为`not_supported`、`partial`、`partial`，没有晋级，因此未重算局部簇。失败的3篇包含H4中较强的2018年和2017—2021年数据候选，所以这次结果只能解释为部分完成，不能据此认定全部6篇均不支持请求。实现随后增加`completed_partial_no_changes`状态和新代际续跑。

`hierarchical-incremental-6papers-20260809-v3`以v2为父代际，已验证能够复用3篇成功Understanding并只重试3篇失败论文。外部供应商随后对3次单篇重试及候选裁决返回HTTP 402，未产生新语义结果；运行按`failed`保留。API恢复后应继续从v2创建新代际，因为v3没有新增可复用的成功Understanding。

供应商恢复后，v4完成5篇候选裁决并晋级2篇，只重算`operation-baseline`，但全局归并因维度来源字段不一致被拒绝。H5随后增加全局阶段续跑：父代际已有完整局部批次时，不再重复Understanding、裁决和局部综合，只重新运行全局归并。

v7最终完成全部6篇候选的Understanding和裁决。严格复核后仅《三峡河段通航调度需求分析》晋级：该文直接分析2017—2021年客货运输量、船舶通过量和船舶大型化趋势。H5为此只重算`operation-baseline`，继续复用`dispatch-optimization`和`facility-coordination`。

同一v7局部批次的全局归并前两次分别因局部维度重复映射和主题簇来源字段不一致被合同拒绝，第三次运行`hierarchical-incremental-6papers-20260810-v9`成功。最终15个局部维度全部进入6个全局维度，形成3条跨簇关系、4个全局缺口和3个后续回看请求，覆盖率1.0；全局归并使用12,759 tokens。H5状态为`completed`，6条候选分配均已裁决，无未处理候选。
