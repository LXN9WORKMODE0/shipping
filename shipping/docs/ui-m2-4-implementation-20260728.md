# 可视化工作台 M2.4 实施记录

日期：2026-07-28

## 结论

M2.4 已完成。工作台现在可以显式选择同一项目中每篇论文的单篇分析 run，
冻结综合 collection，确认外发后运行现有 `llm-topic-synthesis`，并把通过
产物对账的综合 run 原子发布到项目。真实 8 篇论文的完整综合已经成功运行。

## 实现

### 输入冻结

- 综合只接受项目当前 `analysis_run_ids` 中的显式 run，至少选择 2 篇论文。
- 预检重新读取每个来源 run，校验论文身份、状态、主题、Evidence 和输入哈希。
- 冻结来源 run 列表、collection 内容及 SHA-256、模型 profile 和 Non-think 配置。
- Job 使用自有不可变 `input/synthesis_collection.json`，不依赖可变的全局 collection。
- 执行前和发布前均重新对账；输入变化、字节哈希变化或来源 run 不一致时直接失败。

### 执行与发布

- 新增固定 `TopicSynthesisCommandFactory`，API 不能传入任意命令或路径。
- `topic_synthesis` Job 与其他外部任务共用单任务队列。
- Job 保存底层综合 run、请求数、Token、主题、综合单元、章节及错误。
- 综合 manifest、主题、来源 run、输入 SHA 和输出文件全部一致才允许发布。
- 成功后更新项目 `synthesis_run_id` 并增加 revision。
- 最新综合失败时清除项目旧综合引用，避免界面继续暴露过期结果。

### 界面

- 项目页新增独立“跨论文综合”入口，不与 Card 或单篇分析混合。
- 对话框逐篇显示论文、分析 run、状态和 Evidence 数，可显式勾选。
- 预检显示论文数、Evidence 数、模型、Non-think 和 collection SHA。
- 未确认 Card/Evidence 将发送给 DeepSeek 时，前后端均拒绝创建 Job。
- Job 面板显示主题、综合单元、章节、Token 和完整日志。
- 成功发布后，现有综合页直接读取新 run 的主题矩阵、提纲和覆盖审计。

## 真实运行与失败暴露

测试项目：`adversarial-8papers-20260728`，输入为 8 篇当前单篇分析 run，
共 31 条 Evidence。

前三次运行均保留为失败或未发布记录，没有自动重试、降级或复用旧综合：

1. `synthesis-20260728T090306-9dd601bc`：
   模型把跨论文主题标成 `single_source`，违反论文数与关系类型约束，失败。
2. `synthesis-20260728T090754-d627222e`：
   pipeline 生成结果，但 Windows 换行转换导致 Job collection 的实际字节哈希
   与预检 SHA 不一致，严格拒绝发布。
3. `synthesis-20260728T091231-875ad9c1`：
   模型把跨论文综合单元标成 `single_source_context`，违反约束，失败。

据此完成三项修正：

- 主题关系 Schema 与 Prompt 明确：单来源类型必须恰好对应 1 篇论文。
- 提纲关系 Schema 与 Prompt 明确：跨论文单元不得标成单来源上下文。
- 原子 JSON 写入固定使用 LF，并使用唯一临时文件和有限次 Windows
  `PermissionError` 重试；冻结 SHA 对应磁盘实际字节。

最终成功运行：

- UI Job：`synthesis-20260728T091703-5ef0d413`
- 综合 run：`ui-synthesis-20260728T091703-88efd5d8`
- 状态：completed
- 来源论文：8
- Evidence：31
- 主题：6
- 综合单元：12
- 章节：7
- 请求：2
- Prompt Token：14,150
- Completion Token：6,387
- 总 Token：20,537
- Job 运行时间：约 82 秒
- 项目 revision：2 → 3

该运行由工作台项目页完成选择、预检、外发确认和启动，并已在浏览器中读取
新综合页。当前覆盖审计显示 1 条未分配 Evidence、8 条已分配但未进入提纲的
Evidence；这些状态保持可见，没有通过补齐规则隐藏。

## 验收

- 全量 288 个 unittest 通过。
- Python `compileall` 通过。
- 前端 TypeScript 与 Vite 生产构建通过。
- 浏览器验证综合确认框、Job 完成状态和新综合结果页。
- 本地服务由项目独立虚拟环境运行：
  `C:\Users\NING\Desktop\Python\CURSOR\shipping\.venv`。

## 下一批

M3.1 增加完整流程和运行比较，但不改变三个阶段独立运行的主入口：

1. 以冻结 collection 串联 Card、单篇分析和跨论文综合。
2. 展示同一项目不同 run 的输入代际、状态、成本与覆盖差异。
3. 比较前校验 generation 和来源 run 集合，禁止错代际对比。
4. 任一阶段失败即停止后续阶段，并保留已完成子运行和顶层 Job 账本。
