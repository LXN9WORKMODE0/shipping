# 可视化工作台 M3.1 实施记录

日期：2026-07-28

## 结论

M3.1 已完成并通过真实 8 篇完整流程验收：

- 运行页可以严格比较同项目、同阶段的两个 UI Job。
- 项目页新增次级“完整流程”入口，直接调用现有 `full-pipeline`。
- 完整流程从来源、Card、单篇分析到跨论文综合共用一个冻结输入和顶层账本。
- 8 篇 PDF、957 张 Card、8 个单篇 run 和最终综合均已在一个 UI Job 中完成。

## 运行比较

### 可比性规则

比较 API 只接受两个不可变 UI Job ID，并要求：

- 同一项目。
- 同一阶段。
- Card 阶段的论文集合和来源 SHA 一致。
- 单篇分析阶段的 Card generation、输入 SHA 和 materials SHA 一致。
- 综合阶段的论文集合、Card generation、Evidence SHA 和 materials SHA 一致。
- 完整流程的论文集合和来源 SHA 一致。

输入身份不一致时返回 `input_changed`，列出逐篇差异，不计算 Token、耗时、
失败数和产物数量差值。模型、thinking、PDF provider 和配置 SHA 单独列为执行
配置变化，因此可以在相同来源输入上比较不同模型或配置。

### 真实比较

浏览器选择：

- 基准：`synthesis-20260728T090754-d627222e`
- 对照：`synthesis-20260728T091703-5ef0d413`

两次都使用相同 8 篇、31 条 Evidence 的冻结输入，比较判定为可比。对照相对基准：

- Job 状态：failed → completed
- 耗时：约 +8.0 秒
- UI 账本 Token：+20,537
- 失败数：-1
- 主题：+6
- 综合单元：+12
- 章节：+7

基准 Job 的 pipeline 子进程实际曾产生未发布结果，但 UI 在 collection 字节哈希
对账失败后没有把该子结果记为已确认用量，因此 Job Token 为 0。比较页忠实显示
Job 账本，不从未通过验收的子 manifest 猜测回填。

## 完整流程

### 固定命令

新增 `FullPipelineCommandFactory`，只能构造：

```text
main.py full-pipeline
  --collection <Job 自有不可变 collection>
  --workspace <固定 workspace>
  --run-id <预生成不可变 run ID>
  --model-profile <固定 profile>
  --review-config <固定单篇配置>
  --synthesis-config <固定综合配置>
```

API 不能传入任意命令、配置路径、workspace 或 run ID。

### 身份隔离

旧 `full-pipeline` 同时用 `paper_id` 表示论文身份和磁盘目录。M3.1 扩展
`review_pipeline_collection.v1`，允许每篇论文携带可选 `workspace_paper_id`：

- `paper_id`：论文逻辑身份，进入 Card、Evidence 和综合。
- `workspace_paper_id`：项目隔离目录，只用于磁盘存储。

旧 collection 不提供该字段时仍使用 `paper_id`，保持兼容。新 UI collection
始终显式提供隔离 ID，避免不同项目的同名论文相互覆盖。

本次 8 篇项目来自旧验证配置迁移，为保持历史产物可读，继续使用既有论文目录名；
新建 UI 项目使用项目隔离的 `workspace_paper_id`。

### 状态和发布

- 预检冻结全部项目论文、来源 SHA、collection 字节 SHA、模型和两份配置 SHA。
- PDF 项目显示 MinerU；所有项目显示 DeepSeek 与 Non-think。
- 必须明确确认外发后才能创建 Job。
- 顶层三个阶段从运行开始即显式记为 `pending`。
- Card 任一失败时不调用 LLM。
- 单篇任一失败时不使用不完整子集综合。
- 综合失败时不发布正式 `pipeline_result.json`。
- 成功时一次原子发布全部单篇 run ID 和综合 run ID。
- 失败前如果已有新 Card 发布，则清空旧分析索引，防止新 Card 配旧 Evidence。

## 验证

- 真实 8 篇项目预检成功：
  - 8 篇 PDF。
  - MinerU + DeepSeek。
  - 模型 `deepseek-v4-pro`。
  - Non-think。
  - collection SHA
    `sha256:fec553479945bc8d2f8f5712186902091b69d78aedbb53488ccf0c8e34710ba4`。
- 浏览器完整展示 8 篇来源、文件大小、SHA、阶段链路和外发确认。
- 真实本地 Markdown 屏障 Job 验证：
  - 两篇来源中一篇制卡失败。
  - 顶层 Job 失败并保留 manifest。
  - DeepSeek 请求数为 0。
  - `topic_briefs` 和 `topic_synthesis` 保持 `pending`。
  - 不生成正式 `pipeline_result.json`。
- 核心完整流程成功路径使用隔离身份的测试通过。
- 全量 295 个 unittest 通过。
- Python `compileall` 通过。
- 前端 TypeScript 与 Vite 生产构建通过。

## 真实完整流程

### 第一次运行

- UI Job：`pipeline-20260728T115534-883efd29`
- pipeline run：`ui-pipeline-20260728T115534-fdbdd448`
- 状态：failed
- 运行时间：约 10 分 20 秒
- 8/8 Card 完成。
- 单篇阶段：4 篇被旧合同计为纳入，4 篇阻断。
- 综合保持 `pending`，没有使用不完整子集。
- 请求使用：705,581 Token。
- 失败码：`pipeline.topic_brief_stage_failed`。

该运行暴露三个问题：

1. `full-pipeline` 没有接受独立综合已合法接受的
   `completed_with_failures` 状态。
2. 模型把合法枚举之外的 `fact` 用作 `key_points.point_type`。
3. 模型在 Evidence gap 中自行添加 Evidence 未出现的假设数字。

修正方式：

- 将 `completed_with_failures` 加入完整流程可接受状态，不放宽单条 Evidence 校验。
- Prompt 和 JSON Schema 明确禁止 `fact` 等近义标签。
- Prompt 和 JSON Schema 明确禁止在 gap 中创造示例数字，要求使用无数字泛化表述。

### 针对性验证

- UI Job：`brief-20260728T122251-64eb0f49`
- 只重跑两篇真正失败论文，不调用 MinerU、不执行综合。
- 状态：completed
- 总 Token：211,841
- 论文 2：`completed_with_failures`，3 条 Evidence。
- 论文 8：`completed_with_revisit_failure`，6 条 Evidence。

### 第二次运行

- UI Job：`pipeline-20260728T122654-dee8764d`
- pipeline run：`ui-pipeline-20260728T122654-6905c02c`
- 状态：completed
- 运行时间：约 12 分 2 秒
- Card：8/8 完成，共 957 张。
- 单篇分析：8/8 纳入，3 篇 completed、5 篇 completed_with_failures。
- Evidence：29 条。
- 综合：6 个主题、10 个综合单元、8 个章节。
- 覆盖：2 条未归组、1 条已归组但未入纲。
- 综合请求：2 次，18,566 Token。
- 全流程请求：70 次，797,829 Token。
- 顶层 `output/pipeline_result.json` 已发布。
- 项目 revision：5 → 6。
- 项目当前分析索引和综合 run 已一次原子发布。

## 下一批

M3.2 可以开始实现协作式取消与重启对账。M3.1 的运行比较、完整流程、
失败屏障和项目发布已闭合，不再需要业务规则决策。
