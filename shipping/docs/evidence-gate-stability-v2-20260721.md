# Evidence claim 稳定性门禁 v2 实施记录（2026-07-21）

## 结论

`evidence-gate-stability-v2` 已完成代码闭环和离线真实语料规划验证。它解决的是“相同整篇 Prompt 重跑得到完全不同阻断集合”这一工程稳定性问题，不代表现有真实论文已经重新通过语义门禁。

当前 Evidence claim 门禁不再把整篇论文的全部 Evidence 放入一个长数组，也不再把单次模型阻断直接转换成人工任务。新的判定为：

1. 首轮直接支持：通过。
2. 首轮阻断、单条窄上下文复核仍阻断：确认阻断，可进入受约束自动处置或人工裁决。
3. 首轮阻断、窄上下文复核改判直接支持：`gate_disagreement`，以 `support.evidence_claim_gate_unstable` 停止；不视为材料缺陷，不生成伪人工任务。
4. 已有人工或低风险自动覆盖：只有 Evidence ID、verdict 和 unsupported fragments 签名完全一致时生效。

## 技术实现

### 一级章节内稳定小批

- 新增 model profile v2 参数 `evidence_claim_batch_size=12`。
- 输出预算为 `1024 + 512 × Evidence数`，最大8192 tokens；单条复核为1536 tokens，不再沿用整篇门禁的65536上限。
- 有可靠章节图时，Evidence 先映射到引用 Card 的共同章节，再上卷到一级章节；同一章超过12项才按原 Evidence 顺序切批。
- 摘要、英文摘要、参考文献等独立前后置区域保持独立。
- 没有可靠章节图的普通文章不猜标题层级，按论文根节点每12项确定性切批。
- 单批仍执行真实 tokenizer 预算规划，任何批次超上下文直接规划失败。

### 单条窄上下文复核

- 只复核首轮的 `partially_supported/unsupported` 项。
- 每个复核请求只含1个 Evidence Unit。
- 上下文包括当前逐字 quotes、首轮理由、所有被引 Card 的完整 extract，以及每个被引 Card 的前后相邻 Card。
- 相邻 Card 只用于消解术语和指代，不能替代 Evidence Unit 已绑定的 citation。
- 每项至多复核一次，不形成模型循环。

### 内容哈希缓存

缓存身份同时绑定：provider、model、model profile SHA、tokenizer revision、任务阶段、首轮/复核类型、prompt SHA、输入 SHA 和 Evidence ID 列表。

- 命中后重新执行当前 JSON Schema 覆盖校验，再复用结果。
- 缓存记录内部保存完整 SHA；短文件名只用于规避 Windows 路径长度限制。
- 文件存在但身份不一致、内容损坏或覆盖不完整时直接报 `cache.evidence_claim_invalid`。
- 缓存写入失败直接报 `cache.evidence_claim_write_failed`，不会伪装为普通未命中后继续调用。

### 审计产物

每次运行新增：

- `output/evidence_claim_first_pass_reviews.jsonl`
- `output/evidence_claim_confirmation_reviews.jsonl`
- `output/evidence_claim_gate_decisions.jsonl`
- `plan/evidence_claim_support_plan.json`，策略为 `section_bounded_evidence_claim_support_with_confirmation`

覆盖报告新增首轮批数、复核数、确认阻断数、门禁分歧数和缓存命中数。失败中文报告会并列展示两轮相反结论，并明确该项不是已确认论文缺陷。

## 字段职责失败接续

论文综合和未采用证据处置完成后、字段职责门禁开始前，程序现在写入：

- `output/paper_analysis_candidate.json`
- `output/paper_analysis_candidate.provenance.json`

来源记录绑定 paper ID、Card generation、Card 输入 SHA、Evidence 集合 SHA 和候选 SHA。普通完整分析只有在失败代码包含 `role.statement_misclassified` 且上述哈希全部一致时，才能直接作为 `llm-semantic-auto-resolve` 的来源。

这修复了同类问题的后续运行链路。既有23张 Card 旧运行产生于该文件落盘规则之前，不能伪造来源记录；该论文在新代码下重跑后才可直接接续。

## 真实快照离线规划

本轮没有调用外部 API，只读取既有不可变运行的 Card、Evidence 和章节结构：

| 样本 | Card | Evidence | 章节状态 | v2首轮批次 |
|---|---:|---:|---|---|
| 三峡航运遇瓶颈 | 3 | 2 | 无可靠章结构 | 1批：2 |
| 三峡工程给川江航运带来的机遇及对策 | 15 | 21 | 无可靠章结构 | 2批：12、9 |
| 川江滚装运输发展的SWOT分析及对策 | 23 | 23 | 无可靠章结构 | 2批：12、11 |
| 考虑翻坝和天气的长江班轮运网鲁棒优化模型 | 82 | 99 | 可靠章节图 | 12批：2、6、10、12、1、11、12、11、8、12、10、4 |

82张 Card 论文从原先99项整篇单请求变为摘要/一级章节边界内12个确定性请求。原先一次输出99行判断造成的全局耦合已被拆开；实际语义稳定性仍需额度恢复后的真实调用验证。

使用固定 DeepSeek tokenizer 对当前 v2 Prompt 做了离线预算复核：

| 样本 | 首轮请求 | 输入tokens合计 | 单批最大输入 | 单批最大输出预算 | 输入+输出+安全余量最大值 |
|---|---:|---:|---:|---:|---:|
| 3 Card | 1 | 1,595 | 1,595 | 2,048 | 13,643 |
| 15 Card | 2 | 6,673 | 3,894 | 7,168 | 21,062 |
| 23 Card | 2 | 6,499 | 3,510 | 7,168 | 20,678 |
| 82 Card | 12 | 32,735 | 3,650 | 7,168 | 20,747 |

所有请求都远低于100万 token 上下文。代价是82张样本因重复系统指令和 Schema，首轮输入总量高于旧整篇单请求；这是用调用次数和少量输入开销换取局部判定、故障隔离和可缓存性的明确取舍，不应描述为成本下降。

## 当前仍未解决的问题

1. 缓存只能消除相同输入的重复抽签，不能证明首次 `directly_supported` 一定正确。当前只对阻断项复核，直接通过项仍是单次判定。
2. 字段职责门禁仍是论文级单请求，且真实小论文已出现“事实被放入 limitations”“外部规划被放入 core_findings”等分类问题。候选接续已打通，但字段门禁本身尚未做同类稳定性拆分。
3. 15张 Card 论文的3个原阻断和82张 Card 论文的历史漂移项尚未用 v2 做真实复核。平台用量限制仍未解除，不能把离线规划结果写成语义通过。
4. 旧运行不会迁移进新缓存。只有 v2 生成并通过当前 Schema 的响应才可写入缓存。

## 验证

- 新增章节分批、相邻 Card 上下文、确认/分歧决策、内容哈希复用、普通字段失败直接接续等测试。
- 全量回归：168项，全部通过。
- 未修改上游 MD-to-Card、现有真实运行和下游综述语料。

额度恢复后的下一动作固定为：按3、15、23、82张 Card 四个样本执行新运行；先核对批次、token和缓存账本，再比较确认阻断与门禁分歧，不直接生成新人工裁决表。
