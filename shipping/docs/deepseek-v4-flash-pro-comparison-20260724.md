# DeepSeek V4 Flash 与 Pro 对比记录

日期：2026-07-24

## 1. 对比目标

判断当前文献综述 pipeline 是否可以默认使用 `deepseek-v4-flash`，仅在性能差距明显时保留 `deepseek-v4-pro`。

对比输入固定为：

- 主题：三峡船舶积压疏导策略与长江航运组织
- 论文：5 篇
- Evidence：21 条
- collection：`config/pilots/topic-synthesis-api-5papers-20260724.json`
- 模式：Non-think
- Prompt、动态 Schema、输出预算和 tokenizer：相同

## 2. 官方规格

DeepSeek 官方 API 模型 ID 为：

- `deepseek-v4-flash`
- `deepseek-v4-pro`

两者都支持一百万 Token 上下文、Non-think、JSON 输出和最大384K输出。官方价格：

| 项目 | Flash | Pro | Pro/Flash |
|---|---:|---:|---:|
| 缓存未命中输入，每百万 Token | $0.14 | $0.435 | 3.11 |
| 输出，每百万 Token | $0.28 | $0.87 | 3.11 |
| 参数总量/激活量 | 284B/13B | 1.6T/49B | - |

官方 Non-think 基准中，两者在部分一般推理任务上接近，但 Pro 在知识、长上下文和复杂 Agent 任务上整体更强。来源：

- https://api-docs.deepseek.com/quick_start/pricing/
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash

## 3. 供应商 Token 校准

固定 tokenizer 只编码 message chat template。DeepSeek 官方 API 还把请求级 JSON/Non-think 开销计入 prompt usage：

- 短请求：本地16，Flash实际36，差值20。
- 同一短请求：本地16，Pro实际36，差值20。
- 真实主题矩阵：本地4733，Flash实际4753，差值20。

因此 model profile 升级为 `llm.model_profile.v4`，新增显式 `prompt_token_overhead`：

- 原 SiliconFlow profile：0。
- DeepSeek 官方 Flash/Pro：20。

校准后仍要求计划值与 Provider 实际值精确相等，没有改成误差容忍。

## 4. 同条件 A/B

在增强后的固定 Prompt 上，各执行两次独立运行：

### Flash

1. `topic-synthesis-api-5papers-flash-v3-20260724`
   - 主题矩阵通过。
   - 提纲失败：四个主题被重复放入多个主章节。
2. `topic-synthesis-api-5papers-flash-v4-20260724`
   - 主题矩阵 Schema 失败。
   - 原因：导航字段写入阿拉伯数字。

完整通过率：0/2。主题矩阵通过率：1/2。

### Pro

1. `topic-synthesis-api-5papers-pro-v2-20260724`
   - 主题矩阵 Schema 失败。
   - 原因：未归组理由写入阿拉伯数字。
2. `topic-synthesis-api-5papers-pro-v3-20260724`
   - 主题矩阵通过。
   - 提纲失败：一个 transition 段落引用综合单元，但 `evidence_unit_ids` 为空。

完整通过率：0/2。主题矩阵通过率：1/2。

两种模型都没有证明可以仅靠单次 Non-think 请求稳定满足当前全部合同。失败产物均被隔离，没有正式发布。

## 5. 质量差异

两种模型各有一次主题矩阵通过合同，可直接比较：

- Flash 把五篇论文分别组织为五个 `single_source` 主题，没有形成任何跨论文主题关系。
- Pro 将《三峡航运遇瓶颈》和《长江上游地区产业布局及航运适应性研究》的八条 Evidence 合并为“枢纽通过能力瓶颈与供需矛盾”，关系为 `convergent`；其余主题保持单来源。

Pro 的主题矩阵更符合“跨论文综合”目标。其第二次提纲失败是一个局部 transition 段落未附 Evidence；Flash 的提纲失败是多个主题在章节间重复，属于全局结构错误。

## 6. 延迟与费用

选取两边都执行到提纲阶段的可比运行：

| 模型 | 总阶段延迟 | 输入 Token | 输出 Token | 按实际缓存估算费用 |
|---|---:|---:|---:|---:|
| Flash | 31.09秒 | 11130 | 5169 | 约 $0.0030 |
| Pro | 89.97秒 | 10918 | 6936 | 约 $0.0087 |

本样本中 Pro 约慢2.9倍、贵2.9倍。Pro 输出更长，也形成了更实质的跨论文组织。

## 7. 决策

当前不把跨论文主题综合切换为 Flash：

- 价格和速度优势明确。
- 结构化合同通过率没有优于 Pro。
- 通过合同的主题矩阵质量明显弱于 Pro，仍停留在按论文分仓。
- Pro 的失败更接近局部合同边界，而 Flash 出现过关系类型错误和全局章节重复。

默认 profile 改为 `config/models/deepseek-v4-pro-official.json`，与新的 DeepSeek 官方供应商一致。

Flash 保留为后续候选，优先测试这些机械性更强的阶段：

- 论文范围判断。
- Card 相关性筛选。
- 单 Card Evidence 抽取。

跨论文主题矩阵和提纲继续使用 Pro。若后续为结构化输出加入显式、可审计的一次修正机制，应重新做 Flash/Pro 成功率对比。

## 8. v2 合同复验

后续没有加入模型重试或自由修正，而是将可计算字段改为程序编译：

- 单来源主题和综合单元类型按独立论文数生成。
- 章节主题、段落角色、Evidence 和 gap 绑定按综合单元引用生成。
- 未入纲候选单元和未优先缺口保留在审计中。
- 归组冲突、未知身份和缺少 gap ID 等不可推导错误仍直接失败。

同一五篇论文在最终 v2 合同下：

| 模型 | 完整通过率 | 主要结果 |
|---|---:|---|
| Pro | 2/2 | 两次均完整发布，单次约75秒 |
| Flash | 0/2 | 一次归组集合冲突，一次缺口单元没有 gap ID |

因此原决策不变：跨论文主题综合继续默认使用 Pro。Flash 的速度优势没有转化为当前任务的可发布成功率。详细合同变更、失败分类和覆盖审计见 `docs/topic-synthesis-contract-v2-20260724.md`。
