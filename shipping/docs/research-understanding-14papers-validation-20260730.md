# Paper Understanding 14篇验收记录

## 当前结论

- 代码级Task 1至Task 4已完成。
- 14篇显式样本的论文身份、Card代次、冻结Card指纹、Topic Review主题和Evidence引用全部通过本地快照校验。
- 14篇完整输入均能装入当前DeepSeek V4 Pro 100万Token上下文，不需要截断、拆批或缩减字段。
- 用户已明确授权14篇完整Markdown、全部Card和已有Evidence发送给外部DeepSeek。
- 短、中、长三篇首轮验收已经完成。短篇和长篇一次通过；中篇首次因模型输出空的`contribution_indexes`被合同拒绝，修订Prompt后使用新run ID通过。失败运行完整保留，未做结果修补。
- 14篇均已形成被接受的显式运行，状态全部为`completed`，`finish_reason`全部为`stop`。
- 当前合同重新回放14份原始模型响应后，结果与已发布JSON逐项一致；全部material/evidence引用有效。
- Task 5通过，可以进入Research Landscape。

## 验收对象

- 综述主题：三峡枢纽通航能力的提升方法
- UI项目：`review-20260728`
- 显式配置：`config/pilots/research-understanding-14papers-20260730.json`
- 模型配置：`config/models/deepseek-v4-pro-official.json`
- 输出预算：32768 Token
- 安全余量：10000 Token
- 上下文窗口：1000000 Token

## 本地输入预算

| 论文 | 规模 | Card | Evidence | 完整Markdown字符 | 实际Prompt Token | 预算结果 |
|---|---:|---:|---:|---:|---:|---|
| 充分发挥三峡船闸通过能力的途径 | 短 | 6 | 5 | 4228 | 8631 | 通过 |
| 船舶过坝优化调度辅助决策系统研究 | 中 | 22 | 6 | 7115 | 16894 | 通过 |
| 船舶到闸模型及排档优化算法研究 | 长 | 164 | 5 | 107024 | 132124 | 通过 |
| 船型标准化率对三峡船闸实际通过能力的影响 | 短 | 2 | 1 | 8482 | 7535 | 通过 |
| 《长江三峡—葛洲坝水利枢纽通航指标体系》的应用 | 短 | 10 | 4 | 3863 | 10178 | 通过 |
| 船舶积压常态化下的三峡枢纽挖潜扩能措施研究 | 短 | 10 | 5 | 3663 | 10447 | 通过 |
| 2013年三峡坝区通航形势分析 | 中 | 18 | 1 | 4433 | 10654 | 通过 |
| 船舶大型化条件下的船闸管理对策 | 中 | 19 | 4 | 3816 | 11246 | 通过 |
| 川江及三峡库区标准化船舶统计分析 | 中 | 22 | 4 | 3985 | 12151 | 通过 |
| 船舶过闸组织方式对三峡船闸运行效率的影响 | 中 | 23 | 5 | 7059 | 16569 | 通过 |
| “双碳”行动下三峡库区智能航运示范建设研究 | 中 | 26 | 4 | 7661 | 16035 | 通过 |
| 船舶过闸服务智能管控一体化平台研究 | 中 | 26 | 6 | 7326 | 17714 | 通过 |
| 船舶积压条件下三峡过坝运输组织优化研究 | 长 | 199 | 5 | 80844 | 126280 | 通过 |
| 船舶积压条件下三峡坝前转运港口分流能力仿真研究 | 长 | 257 | 2 | 82386 | 142738 | 通过 |

最大输入为142738 Token。加入32768 Token输出预算和10000 Token安全余量后，总计划量为185506 Token，占100万Token上下文的18.6%。

## 三篇首轮真实验收顺序

1. 短篇：充分发挥三峡船闸通过能力的途径。重点检查建议性论述是否被误写成工程实施或现场验证。
2. 中篇：船舶过坝优化调度辅助决策系统研究。重点检查系统设计、算法方法和工程应用层次是否被正确区分。
3. 长篇：船舶到闸模型及排档优化算法研究。重点检查算法benchmark、仿真结果、论文多问题结构和限制是否被完整识别。

三篇通过后再按配置顺序运行其余11篇。失败运行保留原始请求、响应或输入错误，不截断全文、不减少Card、不降低Schema要求。

## 首轮三篇真实结果

### 短篇：充分发挥三峡船闸通过能力的途径

- 运行：`understanding-pilot-short-20260730`
- 状态：完成
- Token：输入8631，输出1964，`finish_reason=stop`
- 判断：正确识别为定性影响因素分析、概念论证和治理/调度建议；所有贡献的验证水平为`none`，没有把建议写成工程实施。
- 来源：5项贡献均绑定Card；4项同时绑定已有严格Evidence，未强迫所有贡献绑定Evidence。

### 中篇：船舶过坝优化调度辅助决策系统研究

- 首次运行：`understanding-pilot-medium-20260730`
- 首次状态：失败
- 失败代码：`schema.paper_understanding_invalid`
- 原因：模型新增一条只基于论文背景的综述用途，但给出空的`contribution_indexes`。输入完整、`finish_reason=stop`，不是截断问题。
- 处理：不自动删除或补写该用途；Prompt明确“无法绑定具体贡献的用途不要输出，禁止空索引”，使用新run ID重新调用。
- 复验运行：`understanding-pilot-medium-20260730-v2`
- 复验状态：完成
- Token：输入16942，输出1702，`finish_reason=stop`
- 判断：系统架构和优化模型被识别为`system_design`或保守的`recommendation`，验证水平为`none`，没有把设计方案误写为已部署系统。

### 长篇：船舶到闸模型及排档优化算法研究

- 运行：`understanding-pilot-long-20260730`
- 状态：完成
- Token：输入132172，输出2762，`finish_reason=stop`
- 输入：完整107024字符Markdown、164张Card和5条Evidence，一次提交，未拆批。
- 判断：识别出动态因子模型、混合分布EM估计、到闸交通流仿真和船闸排档分解算法；排档算法贡献标为`algorithm_benchmark`，没有写成工程实施。
- 限制：保留了作者明确限制和审阅推断限制，包括指标选择不足、EM初值敏感、大船条件下算法间隙利用问题、20艘测试样本偏小及部分混合模型未通过K-S检验。

## 全部14篇终审

被接受的运行ID已经写入`config/pilots/research-understanding-14papers-20260730.json`。旧运行不会由后续阶段按时间自动选择。

| 序号 | 论文 | 接受运行 | 状态 | 引用Card/全部Card | 使用Evidence |
|---:|---|---|---|---:|---:|
| 1 | 充分发挥三峡船闸通过能力的途径 | `understanding-pilot-short-20260730` | 完成 | 6/6 | 5 |
| 2 | 船舶过坝优化调度辅助决策系统研究 | `understanding-pilot-medium-20260730-v2` | 完成 | 14/22 | 3 |
| 3 | 船舶到闸模型及排档优化算法研究 | `understanding-pilot-long-20260730` | 完成 | 31/164 | 3 |
| 4 | 船型标准化率对三峡船闸实际通过能力的影响 | `understanding-pilot-04-standardization-rate-20260730` | 完成 | 1/2 | 1 |
| 5 | 《长江三峡—葛洲坝水利枢纽通航指标体系》的应用 | `understanding-pilot-05-indicator-system-20260730-v2` | 完成 | 10/10 | 4 |
| 6 | 船舶积压常态化下的三峡枢纽挖潜扩能措施研究 | `understanding-pilot-06-capacity-measures-20260730` | 完成 | 10/10 | 5 |
| 7 | 2013年三峡坝区通航形势分析 | `understanding-pilot-07-traffic-situation-20260730-v2` | 完成 | 10/18 | 1 |
| 8 | 船舶大型化条件下的船闸管理对策 | `understanding-pilot-08-large-vessel-management-20260730` | 完成 | 18/19 | 3 |
| 9 | 川江及三峡库区标准化船舶统计分析 | `understanding-pilot-09-standardized-vessels-20260730` | 完成 | 8/22 | 4 |
| 10 | 船舶过闸组织方式对三峡船闸运行效率的影响 | `understanding-pilot-10-lock-organization-20260730` | 完成 | 13/23 | 3 |
| 11 | “双碳”行动下三峡库区智能航运示范建设研究 | `understanding-pilot-11-smart-shipping-20260730` | 完成 | 20/26 | 2 |
| 12 | 船舶过闸服务智能管控一体化平台研究 | `understanding-pilot-12-smart-platform-20260730-v2` | 完成 | 11/26 | 5 |
| 13 | 船舶积压条件下三峡过坝运输组织优化研究 | `understanding-pilot-13-transport-optimization-20260730` | 完成 | 48/199 | 2 |
| 14 | 船舶积压条件下三峡坝前转运港口分流能力仿真研究 | `understanding-pilot-14-port-diversion-20260730` | 完成 | 29/257 | 0 |

### 失败和淘汰记录

- `understanding-pilot-medium-20260730`：失败。空`contribution_indexes`被Schema拒绝。
- `understanding-pilot-05-indicator-system-20260730`：运行完成但被语义终审淘汰。单项贡献混合了已上线系统和后续管理建议。
- `understanding-pilot-07-traffic-situation-20260730`：运行完成但被语义终审淘汰。已试行吃水标准被误标为建议。
- `understanding-pilot-12-smart-platform-20260730`：运行完成但被语义终审淘汰。工程实施与概念验证水平不一致。

后三篇均使用新不可变run ID重新调用。程序没有改写旧输出，也没有通过删除字段使旧结果过关。

### 验收条件核对

- 14篇每篇都有显式接受运行，且全部状态为`completed`。
- 当前动态Schema重新验证全部原始响应，14份结果均与发布对象一致。
- Paper Understanding Schema不存在跨论文关系字段；回放扫描未发现相关字段。
- `simulation_result`全部使用`simulation`验证，未标成工程实施。
- `engineering_implementation`全部使用`engineering_application`。
- `recommendation`只使用`none`或`conceptual`，未标成经验事实。
- 报告直接展示Card标题、Markdown行号和原文摘录。
- 164、199和257张Card的三篇长论文分别引用31、48和29张Card，但只使用3、2和0条已有Evidence，证明结果不是少量Evidence的复述。
- 未引用Card继续保存在每篇运行的`audit/unreferenced_materials.jsonl`，不会从后续章节知识包中消失。
