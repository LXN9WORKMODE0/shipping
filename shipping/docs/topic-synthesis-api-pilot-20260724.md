# 跨论文主题综合 API 试验记录

日期：2026-07-24

## 1. 试验目标

使用真实 DeepSeek Non-think API，对比只读和模拟测试，验证：

1. 新单篇论文能否生成当前 v2 主题简报。
2. 五篇论文能否通过严格来源快照进入跨论文主题矩阵。
3. 真实 `theme_map` 和 `outline` 输出能否通过动态 Schema 与覆盖合同。

本轮不修改上游 Markdown/Card，也不把失败运行或旧合同不合格来源加入正式综合。

## 2. 输入准备

原四篇 smoke 来源之外，选择《扩大三峡船闸通航能力的措施研究》作为第五篇。该论文只有四张 Card，且与三峡船闸扩能和积压疏导直接相关。

新增单篇运行：

- run ID：`topic-brief-api-capacity4-v2-20260724`
- 状态：`completed`
- 论文相关性：`core`
- Card：4 张，全部入选
- Evidence：4 条
- 回查：未触发
- API 请求：6 次
- Token：prompt 21184，completion 2549，total 23733

准备阶段曾产生一次失败运行 `topic-brief-api-capacity4-20260724`：命令把 `--env-file` 错指向上级目录，程序因缺少 API URL 在输入阶段失败，未发出模型请求。该运行保留为配置错误审计，修正路径后使用新 run ID 成功。

单篇报告保留了原始 OCR 损伤，例如“40 i 日”，没有把不可靠字符自动修复成新事实。

五篇 collection：

- `config/pilots/topic-synthesis-api-5papers-20260724.json`
- 来源状态：5 篇均为 `completed`
- Evidence：21 条
- 主题矩阵输入：4733 Token
- 默认计划最大输出：8128 Token
- 上下文窗口：1000000 Token
- 输入快照：`sha256:40247c47eed454e2e3648a8e6710b2f71ab35d7c7a788a65baf03586aeec6ea9`

另一个旧 v2 候选 `topic-brief-auto-revisit-countermeasure19-20260723` 在调用前被严格重验拒绝：其 caution 含“2010年”，不满足当前导航字段禁止数字的合同。该来源没有被绕过或加入 collection。

## 3. 跨论文调用结果

第一次使用默认配置：

- run ID：`topic-synthesis-api-5papers-20260724`
- 阶段：`theme_map`
- planned input：4733 Token
- planned max output：8128 Token
- Provider：HTTP 402
- 原始错误：`account balance is insufficient`

为区分余额不足和最大输出预留过高，建立了仅用于试验的显式低预留配置：

- `config/pilots/topic-synthesis-api-low-reserve-20260724.json`
- 主题矩阵计划最大输出：4064 Token
- 不修改生产默认配置

第二次低预留调用：

- run ID：`topic-synthesis-api-5papers-lowreserve-20260724`
- 阶段：`theme_map`
- planned input：4733 Token
- planned max output：4064 Token
- Provider：仍为 HTTP 402
- 原始错误：仍为 `account balance is insufficient`

两次请求均未被模型实际处理，`actual_prompt_tokens` 和 usage 为空；没有生成主题矩阵、提纲、正式 JSON 或中文报告。

## 4. 当前结论

已经确认：

- API 配置、密钥和网络可用，因为新增单篇简报完整成功。
- 五篇、21 条 Evidence 的严格快照和 Token 规划通过。
- 失败运行正确留下原始响应和阶段账本，并且没有发布半成品。
- 降低最大输出预留后仍为同一余额错误，当前阻断是 Provider 账户余额，不是跨论文 Prompt 或程序合同。

尚未确认：

- DeepSeek 对五篇论文的主题划分质量。
- 主题关系是否符合独立论文证据。
- 提纲、检索方向和中文报告是否可用于实际综述写作。

补充 Provider 余额后，应使用新的不可变 run ID 重新执行五篇 collection。生产默认配置更保守；低预留配置只用于本次余额诊断，不能视为已经验证的正式预算。
