# Execution Roadmap

更新日期：2026-04-19

这份文档只记录后续路线，不重复已经完成的 Phase 1 到 Phase 4。

## Current Position

当前系统已经具备单篇论文的主链能力：

- `convert`：PDF 转 MinerU 原始结果和规范 Markdown
- `parse`：生成章级为主的 `structure.json`、`diagnostics.json`、`outline.json`
- `summarize`：生成 `summary_tree.json`、`overview.json`、`review_notes.json`
- `refinement`：低置信节点可走 LLM 修正并保留 fallback

当前路线的核心判断是：

- 先把“批量稳定跑”和“章级解析可靠”做实
- 再做跨论文整合
- 最后再做面向高级 LLM 的综述消费层

## Planning Rules

- 章级结构优先于细粒度结构。
- `structure.json` 继续作为唯一结构真相源。
- `summary_tree` 继续围绕章级 `body` 节点生成。
- 新能力必须建立在真实语料可验证的基础上，不靠单次演示样本判断。
- 每一阶段都必须留下可复跑的命令、持久化产物和回归测试。

## Phase A: Recover Green Baseline

目标：先恢复当前代码基线的稳定性，再继续向前推进。

要做的事：

- 修复当前已知回归，保证 `pytest` 全绿。
- 确认章级压缩、summary 2.1、长学位论文 parse 路径之间没有互相打架。
- 把当前“章级结构树”作为新的显式基线写进测试，而不是继续兼容旧的小节级预期。

完成标准：

- `pytest -q` 全绿。
- 期刊样例、长学位论文样例、plain heading 样例都能稳定通过。

## Phase B: Batch Reliability And Fault Tolerance

目标：把系统从“能跑”提升到“可以批量稳定跑”。

要做的事：

- 将批处理改成按论文、按 stage 独立执行的任务模型。
- 为 `convert / parse / summarize` 分别设置硬超时、失败分类和可控重试。
- 扩展 `run.json`，记录：
  - `current_stage`
  - `stage_status`
  - `attempt_count`
  - `last_error`
  - `last_error_stage`
  - `durations`
  - `warnings`
- 支持中断后 resume，而不是每次从头重跑。
- 为 audit 和 batch 共用一套任务状态和日志格式。

完成标准：

- 单篇卡住不会拖死整批。
- 中断后可从已有状态恢复。
- 每篇失败都能定位到具体 stage 和错误类型。

## Phase C: Parse Reliability

目标：让章级结构树在真实中文论文上尽量稳定。

要做的事：

- 继续收紧前置页、目录、页码尾巴、拆裂标题的识别逻辑。
- 明确“章节点怎么选”，避免次级节点冒充章。
- 处理重点场景：
  - 中文期刊的 `0 引言 / 一、 / 1.`
  - 学位论文的 `第X章`
  - 带页码尾巴的目录标题
  - plain heading 型正文
- 让 `diagnostics.json` 更贴近章级目标，补足：
  - `chapter_selection_trace`
  - `body_start_rejections`
  - `profile_fallback_used`

完成标准：

- 大多数期刊文章能稳定输出章级结构。
- 长学位论文不会再大面积把目录条目误当正文。
- 章节点数量基本符合人工预期。

## Phase D: Summary Reliability

目标：让章级摘要树真正服务综述准备。

要做的事：

- `summary_tree` 只保留章级 `body` 节点。
- `overview.quick_take` 只基于章级摘要，不让前置页污染主结论。
- `review_notes.body_section_notes` 以章为主，而不是再向细粒度扩展。
- 优化长章摘要的长度控制和 fallback 行为。
- 保证摘要阶段失败不会污染解析阶段已落盘产物。

完成标准：

- 长学位论文的摘要树不再混入学校名、声明页、拆裂标题。
- `quick_take` 更像章级综述入口，而不是段落拼接。
- 长文档摘要耗时明显收敛。

## Phase E: Corpus Integration Layer

目标：把单篇工作区提升为可用于综述的跨论文语料层。

要做的事：

- 新增 corpus 层，统一汇总每篇论文的章级节点。
- 建议新增：
  - `workspace/_corpus/manifest.json`
  - `workspace/_corpus/papers.jsonl`
  - `workspace/_corpus/chapters.jsonl`
- 定义章级语料单元，例如 `ChapterCard`，至少包含：
  - `paper_id`
  - `paper_title`
  - `paper_type`
  - `year`
  - `chapter_id`
  - `chapter_title`
  - `chapter_order`
  - `chapter_summary`
  - `review_note`
  - `content_ref`
  - `confidence_flags`
- 支持 corpus rebuild，不依赖人工整理。

完成标准：

- 多篇论文的章级节点可以统一检索、过滤和排序。
- corpus 层可以在不重跑 MinerU 的情况下重建。

## Phase F: LLM Consumption Layer

目标：让高级 LLM 不再直接吃整批论文，而是消费已经整理好的综述素材包。

要做的事：

- 提供面向综述任务的检索和打包接口。
- 输入是：
  - 一个综述主题
  - 若干子问题
  - 可选过滤条件
- 输出不是直接成文，而是多层素材包，例如：
  - `topic_overview_pack`
  - `paper_shortlist`
  - `chapter_evidence_pack`
  - `claim_evidence_map`
  - `review_outline_seed`
- 控制 token 预算，把输入约束在章级摘要和必要证据片段。

完成标准：

- 高级 LLM 可以基于素材包组织综述，不需要直接读取整批单篇 JSON。
- 生成内容能回溯到 `paper_id + chapter_id + content_ref`。

## Phase G: Review Workflow

目标：在语料层和消费层稳定后，形成真正的综述工作流。

要做的事：

- 支持生成：
  - 综述提纲
  - 章节级证据矩阵
  - 综述初稿
  - 冲突观点列表
  - 缺口与未来研究点
- 保持“人审阅在前、模型组织在后”的工作方式。
- 所有结论都要能回溯到章级证据。

完成标准：

- 系统能从多篇论文稳定生成可编辑、可追溯的综述素材，而不是一次性黑盒输出。

## Recommended Execution Order

实际执行顺序按下面走：

1. Phase A
2. Phase B
3. Phase C
4. Phase D
5. Phase E
6. Phase F
7. Phase G

其中：

- `Phase A-D` 解决“底层处理器是否可靠”
- `Phase E-F` 解决“跨论文整合和高级 LLM 消费”
- `Phase G` 才是“综述工作流本身”

## Immediate Next Step

当前应先进入 `Phase A`：

- 修复现有回归
- 恢复测试全绿
- 确认章级结构树是当前唯一有效基线
