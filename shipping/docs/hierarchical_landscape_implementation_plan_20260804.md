# 层级论文综合器实施方案

日期：2026-08-04

## 目标

解决旧Research Landscape只能把全部Paper Understanding放入一次调用的问题，同时保证全部入选论文可追踪、允许有限跨主题归属、局部失败不阻断全局，并能从主题缺口回看尚未深度处理的论文池。

## 数据链

```text
筛选论文池
→ 冻结的Paper Understanding集合
→ 主题路由计划
→ 多个局部Research Landscape
→ 全局Landscape归并
→ 主题缺口与回看账本
→ A2章节写作与来源核验
```

## 稳定边界

1. 一篇论文默认进入一个主题簇，最多进入两个；不要求唯一主题。
2. 未进入主题簇的论文必须给出显式原因，不能从账本消失。
3. 单个主题簇默认不超过40篇，且至少2篇。
4. 局部综合继续复用现有Research Landscape合同和严格来源回放。
5. 全局综合只读取局部Landscape投影，不读取全部Paper Understanding。
6. 局部与全局输出保留paper_id、contribution_id和local landscape ID。
7. 回看机制只增量补充相关主题，不重新运行无关主题。

## 实施阶段

### H1：路由与局部集合

- 路由合同；
- 论文逐篇归属账本；
- 跨主题上限；
- 未分配论文记录；
- 自动生成旧Runner可读取的局部collection。

### H2：局部运行控制

- 每簇独立运行；
- 部分成功；
- 续跑与代际对账；
- 局部coverage汇总。

### H3：全局归并

- 局部Landscape压缩投影；
- 跨簇关系与总体结构Schema；
- 所有局部维度必须进入全局维度或显式未映射；
- 不允许全局层凭空生成论文关系。

### H4：自动回看

- 将全局缺口映射为主题簇；
- 从未分配core、supporting和peripheral中生成候选；
- 记录纳入原因和补充代际；
- 增量重跑受影响的局部簇及全局归并。

## 当前实现状态

已完成H1骨架。新增`hierarchical-landscape-plan`命令，用显式路由文件和冻结Understanding collection生成局部collections、逐篇membership账本和覆盖审计。H1不调用外部模型。

已开始H2。新增`hierarchical-landscape-local-run`命令，按主题簇复用旧Research Landscape Runner，支持单簇失败隔离、限量运行和同计划续跑。

首次6篇真实计划重放暴露了展示标题与`paper_id`可能因空格、连字符和OCR格式不同。路由必须读取程序导出的身份目录并使用准确`paper_id`，不得用标题模糊匹配；错误路由已被`paper_unknown`门禁拒绝，没有发布计划。

H1现已在每次运行中导出`input/paper_identity_catalog.jsonl`。路由失败会写入failed manifest和`audit/failures.jsonl`，不再留下无状态目录。

H3已开始实现。新增`hierarchical-landscape-global-run`及独立Schema：全局维度必须完整对账全部局部维度；跨簇关系必须引用两侧局部维度；语料缺口与回看请求分开发布。全局层不读取Markdown、Card或完整Paper Understanding。

## 6篇真实局部综合验证

获得授权后，运行`hierarchical-landscape-6papers-local-20260809-v1`：

| 主题簇 | 论文 | 维度 | 关系 | 缺口 | 总tokens |
|---|---:|---:|---:|---:|---:|
| 通航能力现状与运行基线 | 2 | 6 | 2 | 3 | 11,793 |
| 船闸与枢纽调度优化 | 4 | 4 | 6 | 3 | 20,058 |
| 通航设施检测与资源协同 | 2 | 5 | 1 | 2 | 12,232 |

三个局部运行全部完成，共使用44,083 tokens；6篇论文全部进入局部维度，没有unmapped论文，也没有制造disagreement。两个跨主题论文分别在运行基线/调度优化、调度优化/设施协同中承担不同用途。

H3本地严格加载会从局部正式输出移除程序机器ID，重新执行旧Landscape Schema、论文覆盖和contribution ownership校验，再比较重新生成的稳定ID。三个真实局部Landscape共15个维度，已通过完整依赖链重放。

当前尚未执行真实全局归并调用。H3假客户端已验证全局维度完整对账、跨簇双侧来源、稳定ID、coverage及回看请求账本。

首次真实全局归并`hierarchical-landscape-6papers-global-20260809-v1`被完整性门禁拒绝：15个局部维度中有1个被跨簇关系引用，但既未进入全局维度，也未列入unmapped。失败运行使用10,664 tokens并完整保留。合同随后升级为v2，新增固定15条的`local_dimension_accounting`，要求模型逐维度声明映射状态并与正式输出交叉对账，不由程序自动补齐遗漏。

v2首次运行已经完整覆盖15个维度，accounting中的目标编号也全部一致，但合同实现额外要求mapped项的`reason`必须为null，而Schema允许说明文字，导致内部约束冲突。现已允许mapped项保留解释；unmapped仍强制目标编号为null且必须填写原因。

修正后真实全局运行`hierarchical-landscape-6papers-global-20260809-v3`完成：15个局部维度全部进入7个全局维度，形成2条跨簇关系和4个回看请求，覆盖率1.0，使用12,578 tokens。

H4新增`hierarchical-landscape-lookback`：严格重放全局Landscape与最终筛选账本，把core/supporting筛选投影提交给候选匹配器。每个请求最多12篇候选，一篇论文最多匹配2个请求；没有候选允许显式空结果。候选只发布为建议补充，不自动运行Understanding或修改主题簇。

首次真实回看`hierarchical-lookback-6papers-20260809-v1`使用233,171 tokens。模型对首个请求选入一篇同时明确判断“无法使用”的低置信候选，并填写no_match_reason，违反候选/无匹配互斥合同，运行被拒绝。Schema现已增加条件约束，负面判断只能形成空候选和no_match_reason，不能占用候选名额。

第二次真实回看`hierarchical-lookback-6papers-20260809-v2`暴露了另一项合同缺口：不同请求虽然具有各自的`desired_source_levels`，但Schema共用全体候选ID枚举，模型可以把supporting论文放入仅允许core的请求，直到响应后校验才失败。H4现改为逐请求`prefixItems` Schema，固定请求顺序、请求ID和该请求允许使用的候选集合，约束不再依赖响应后的补救校验。

修正后`hierarchical-lookback-6papers-20260809-v3`完成：4个回看请求中1个匹配到6篇候选，3个明确记录现有论文池无匹配；共使用256,982 tokens，其中缓存命中215,552 tokens。匹配请求针对2013年后运输量和船舶特征变化，其中至少3篇直接包含2014年、2018年或2017—2021年数据；另有1篇主要覆盖2012—2013年，只能视为间接背景候选。该结果说明H4适合作为自动建议召回层，但候选不能自动升级为新证据。当前实现因此只发布候选账本，不自动运行Paper Understanding、不修改主题簇，也不触发全局重写。

至此6篇试点的H1路由、H2局部综合、H3全局归并和H4自动回看已经形成可运行闭环。下一阶段若要将回看候选真正纳入综述，应另行定义“候选再理解与受影响主题增量重算”的生产策略；这不是当前H4建议层的隐式行为。

## H5：候选晋级与受影响主题增量重算

H5已定义为独立代际，不修改H4候选账本，也不复用旧局部Runner的同计划续跑语义。原因是候选晋级会改变局部collection，旧续跑要求`plan_sha256`完全一致，不能用于内容已变化的主题簇。

H5流程为：候选逐篇Paper Understanding → 请求级`direct/partial/not_supported`裁决 → 仅`direct`且中高置信候选晋级 → 只重算目标主题簇 → 复用未受影响簇 → 重新全局归并。每请求默认最多晋级4篇。Understanding失败候选显式记录但不阻断其他论文；受影响局部簇未全部成功时禁止生成新全局结果。

新增命令`hierarchical-landscape-incremental-run`及独立配置、晋级账本和中文报告。真实H4 v3已完成只读血缘预检：6条候选分配对应6篇论文，全部只影响`operation-baseline`，因此后续真实H5最多重算1个簇并复用另外2个簇。

H5首次真实运行v2中，6篇候选有3篇Understanding成功，裁决结果为1条`not_supported`和2条`partial`；另外3篇因模型Schema输出错误失败。由于失败论文中包含较强的后续年度数据候选，该代际实质上是部分完成，不能将零晋级解释为全部候选均无效。H5已增加部分完成状态与父代际续跑：成功Understanding复用，失败论文重新调用。

续跑v3已证明选择性复用有效，但外部供应商对3篇重试及后续裁决均返回HTTP 402，未产生新结果。该阻塞来自供应商计费状态；本地流程保持失败隔离，没有重算局部簇或发布新全局Landscape。

供应商恢复后的最终H5生产验证已完成。6篇候选全部完成Understanding与请求级裁决，最终只晋级《三峡河段通航调度需求分析》；程序只重算`operation-baseline`并复用另外2个主题簇。针对全局模型偶发引用合同失败，H5新增“完整局部批次→仅重试全局归并”的阶段续跑，不重复前序模型调用。最终运行`hierarchical-incremental-6papers-20260810-v9`完成：15个局部维度全部映射，形成6个全局维度、3条跨簇关系、4个全局缺口和3个回看请求，覆盖率1.0。

## 扩容验证结论

18篇基线论文的H1至H4已完成真实运行，H5从23条回看候选中晋级9篇，最终形成27篇论文的全局综合。扩容验证促使全局合同升级至v5：模型只选择局部维度及合法主题簇对，维度所属簇、论文集合和跨簇端点由程序确定性派生，避免模型复制长ID和所有权字段。H4可严格迁移重放旧版全局结果。

H5续跑现按Understanding运行ID冻结复用既有裁决，防止相同语义输入因重复调用产生晋级漂移；失败局部批次可以回到已完成Understanding继续，而不会错误进入全局重试。最终运行`hierarchical-incremental-18papers-20260810-v10`完成：23/23候选已裁决、9篇晋级、3簇重算、1簇复用；28个局部维度全部归入12个全局维度，形成6条跨簇关系，覆盖率1.0。

这证明H1至H5的主链在多主题、跨主题和增量召回场景下已经闭环，但不等于模型输出一次成功率已经充分稳定。当前主要剩余工程问题是局部Landscape仍要求模型复制较长的contribution ID，扩容运行中出现过漏字符和重复ID；下一轮应把局部贡献也改为短别名选择、程序回填正式ID，并支持只重试失败的受影响簇。
