# LLM 语义自动处置与跨论文验证审计（2026-07-21）

## 一、定性结论

本轮实现的“自动处置执行器”基本可用，但当前“整篇 Evidence claim 一次性硬门禁”不能作为稳定的发布判据。

- 原82张卡论文的6个人工项，均能落入明确的自动动作：补引文2项、映射到已验证重复 Evidence 1项、低风险门禁覆盖1项、删除错位重复陈述2项。
- 六项动作执行后，数值引用、Evidence ID、论文陈述引用和字段删除均能由代码侧完整校验；字段职责复核48/48通过。
- 但是，同一份98个 Evidence、相同 prompt hash、相同 input hash 和相同 request_id 的两次 Evidence claim 复核，第一次产生3个阻断，第二次产生15个阻断，两个阻断集合交集为0。
- 因此，大量人工工作并不完全来自材料缺陷，其中相当一部分来自门禁模型自身的判定漂移。继续逐轮自动追逐会把随机波动伪装成材料问题。
- 三篇额外真实论文均未一次走完整条链，失败点分别位于综合输出形状、Evidence claim、数值引用和字段职责。Card/Evidence 输入完整性不是本轮主要瓶颈。

当前状态应表述为：**受约束自动处置机制已经建立；LLM语义硬门禁的稳定性尚未过关，暂不适合把每个阻断都升级为人工任务。**

## 二、本轮实现

新增独立命令：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-semantic-auto-resolve `
  --workspace shipping/workspace `
  --audit-run-id <语义审计或上一轮自动处置run_id> `
  --run-id <新run_id> `
  --provider openai-compatible `
  --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json
```

运行目录固定为：

```text
workspace/_llm_analysis/semantic_auto_resolutions/<run_id>/
```

每次运行不可覆盖，保存输入快照、动态 JSON Schema、请求、原始响应、解析结果、动作、复核结果、失败账本和中文报告。

### 允许的自动动作

1. `repair_citations`
   - 只能从当前 Card 和相邻 Card 的精确 quote candidate 中选择。
   - 必须至少增加一条新引文，不能把原引文重新包装成“已修复”。
   - 重新生成 Evidence anchor 和 Evidence ID，并更新论文级全部引用。

2. `replace_with_supported_evidence`
   - 只能映射到同篇论文中已通过 claim 门禁的 Evidence。
   - 候选必须位于当前/相邻 Card，claim 二元组相似度不低于0.35。
   - 替代 Evidence 必须保留所有受影响陈述中的数字；否则不进入候选集。

3. `keep_gate_override`
   - 只用于低风险自然语言等值改述。
   - 数字、公式、完成状态、因果、模型含义、最优性和鲁棒性解释等不允许自动覆盖。
   - 覆盖只对相同 Evidence ID、verdict 和 unsupported fragments 签名有效。

4. `delete_duplicate_statement`
   - 只删除数组字段中的错位副本。
   - 正确字段候选必须与错位陈述共享至少一个 Evidence ID。
   - 按陈述文本和 Evidence 组合重新定位，避免连续删除造成数组下标漂移。

5. `human_required`
   - 任何超出上述边界的问题必须保留。
   - 即使后续模型偶然改判为通过，规划器明确标记的人工项也不会静默消失。

### 发布边界

- 不修改上游 Markdown、Card 或 corpus。
- 不写入下游综述语料。
- 只有 Evidence claim、字段职责和陈述支持全部通过，才生成 `validated_paper_analysis.json`。
- 该文件仍标记为 `validated_candidate`，不是正式发布结果。

## 三、82张卡论文回放

论文：`考虑翻坝和天气的长江班轮运网鲁棒优化模型`

来源审计：`semantic-gates-large82-calibrated2-20260717`

### 原6项的自动分流

| 原问题 | 自动动作 | 结果 |
|---|---|---|
| 摘要 Evidence 缺“构建模型、实例验证”引文 | 补充摘要后半段精确引文 | 生成新 Evidence ID |
| “研究有很多”概括为“已有大量研究” | 低风险等值覆盖 | 保留原 Evidence，保存覆盖签名 |
| 式4.25只有公式、缺含义说明 | 保留公式并补相邻正文解释 | 同时保留公式编号和语义说明 |
| 表格4046万元不足以单独支持“差不到5%、解鲁棒” | 映射到作者正文直接说明 Evidence | 删除重复表格 Evidence |
| `review_uses` 复述研究目标 | 删除错位副本 | `research_focus` 保留共享 Evidence |
| `review_uses` 复述M1/M2/M3关系 | 删除错位副本 | `methods` 保留共享 Evidence |

自动处置后：

- Evidence：99 → 98
- 论文陈述：50 → 48
- 字段职责复核：48/48 `field_aligned`
- 原6项人工需求：0项需要按原二选一方式裁决

### 门禁稳定性反例

对自动处置后的完全相同候选，执行了两次 Evidence claim 复核：

| 指标 | 运行 r4 | 运行 r7 |
|---|---:|---:|
| request_id | `evidence_claim_support_9cffc965d8cd63b1` | 相同 |
| prompt_sha256 | `0d58447e...9766df` | 相同 |
| input_sha256 | `757bebad...557b` | 相同 |
| prompt tokens | 19,210 | 19,210 |
| 阻断数 | 3 | 15 |
| 两次阻断交集 | 0 | 0 |

r4的3项在r7中全部改判为直接支持；r7又新增15项阻断，涉及表格行上下文、程度词、公式解释和同义概括等。

这不是输入、token、模型路由或程序版本造成的差异，而是相同 Non-think 模型调用的语义判定不稳定。当前不能把这些结果直接转换成18个人工任务。

## 四、其他三篇真实论文

### 1. 三峡航运遇瓶颈（3张Card）

- Evidence：2个
- Evidence claim：2/2通过
- 首轮失败：综合器把数组字段输出为 `{"items": [...]}`。
- 修正：系统提示明确数组字段必须直接输出 `[...]`，禁止 `items` 包装。
- 显式重试后：综合 Schema 通过。
- 最终失败：2条 `limitations` 实际是论文事实/基础设施现状，不履行局限字段职责。

结论：小论文并未受 token 限制，主要问题是综合输出形状和字段分类。

### 2. 三峡工程给川江航运带来的机遇及对策（15张Card）

- Evidence：21个
- Evidence claim：18个直接支持，3个部分支持
- 阻断示例：引文写“客运和货运”，门禁认为不能概括为“客货运”。
- 未进入论文综合。
- 原计划做同输入门禁复测，但API调用被平台用量上限拒绝，未取得第二次结果。

结论：中小论文同样存在语义等价过度严格问题，不是82张卡特例。

### 3. 川江滚装运输发展的SWOT分析及对策（23张Card）

- Evidence：23个
- Evidence claim：23/23通过
- 首轮失败：`review_uses` 自行加入“2011年前后”，绑定 Evidence 不含2011，被代码侧数值校验阻断。
- 显式重试后：数值问题消失。
- 最终失败：1条外部发展规划预测被放入 `core_findings`，字段职责门禁判错位。

结论：确定性数值校验有效；字段职责仍需要自动删除/移动机制，但当前普通分析失败运行没有输出可供自动处置器消费的候选文件。

## 五、跨论文汇总

| 论文 | Card | Evidence | Claim门禁 | 最终停止点 |
|---|---:|---:|---|---|
| 三峡航运遇瓶颈 | 3 | 2 | 2/2通过 | 字段职责：2项错位 |
| 三峡工程给川江航运带来的机遇及对策 | 15 | 21 | 18通过、3部分支持 | Evidence claim |
| 川江滚装运输发展的SWOT分析及对策 | 23 | 23 | 23/23通过 | 字段职责：1项错位 |
| 考虑翻坝和天气的长江班轮运网鲁棒优化模型 | 82 | 98（处置后） | 重复门禁3/15项漂移 | Evidence claim稳定性 |

共同点：

- Card身份、generation、逐字引文、材料覆盖均通过确定性检查。
- 输入 token 预算不是限制因素。
- 失败集中在 LLM 语义判断和论文字段组织。
- 将所有门禁阻断直接交给人工不可扩展，且会包含大量模型自身的波动。

## 六、建议的下一目标

下一步不应继续扩大人工裁决表，也不应继续追逐82张卡的漂移阻断。建议建立 `evidence-gate-stability-v2`：

1. 确定性校验继续作为硬门禁
   - ID、generation、quote精确性、数值、覆盖、Schema、finish_reason 和 token 一致性保持不变。

2. Evidence claim 改为章节内小批核验
   - 每批约8至12个 Evidence，保持同一章节上下文。
   - 结果按 Evidence 内容哈希缓存；相同 Evidence 不重复核验。
   - 避免98项长数组一次输出时的全局判定漂移。

3. 只对首轮阻断项做第二次窄上下文复核
   - 输入完整 Card、相邻 Card、claim、当前逐字引文和首轮理由。
   - 第二次复核只能选择：直接支持、补引文、收窄/删除、明确人工。
   - 两轮意见不一致时记录 `gate_disagreement`，不能直接升级人工缺陷。

4. 先区分“最终分析会使用”与“未使用” Evidence
   - 未引用且存在争议的 Evidence 可显式标为 `excluded/semantic_uncertain`，保留审计，不要求人工逐项裁决。
   - 被论文级陈述引用的争议 Evidence 才进入自动修复或人工队列。

5. 普通分析失败也必须保存候选
   - 字段职责失败前保存不可变 `paper_analysis_candidate.json`。
   - 自动处置器直接消费普通失败运行，删除共享 Evidence 的错位重复项。
   - 避免为了处理1条字段错位而重新综合整篇论文。

## 七、未完成项与外部阻断

- 计划中的15张卡 Evidence claim 同输入复测未执行，原因是平台返回用量上限，下一可用时间提示为2026-07-26。
- 本轮未改动上游 MD-to-Card，也未把任何自动处置结果写入下游综述语料。
- 82张卡论文没有生成正式验证候选；这是门禁稳定性不满足的真实结果，不应改写为“已通过”。

## 八、后续实施状态

本报告第六节建议的 `evidence-gate-stability-v2` 已于同日完成代码实现和离线真实快照规划验证。实现包括一级章节内至多12项的小批门禁、首轮阻断项单条窄上下文复核、严格内容哈希缓存、`gate_disagreement` 独立失败状态，以及字段职责门禁前的不可变候选存档。

详细设计、离线批次结果和仍未解决的风险见 `docs/evidence-gate-stability-v2-20260721.md`。由于API额度仍受限，该状态是“工程实现和离线验证完成”，不是“真实语义门禁已经通过”。
