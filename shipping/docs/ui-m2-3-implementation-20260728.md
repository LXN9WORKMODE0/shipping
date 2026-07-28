# 可视化工作台 M2.3 实施记录

日期：2026-07-28

## 结论

M2.3 已完成。工作台现在可以从项目中选择已完成 Card 的论文，在显示模型、
Card 数、generation 和输入哈希后，要求用户明确确认外发，并逐篇运行现有
`llm-topic-brief`。真实 DeepSeek 调用、产物对账和项目发布均已通过。

## 实现

### 身份与输入冻结

- `paper_id` 继续表示论文逻辑身份。
- `workspace_paper_id` 只表示项目隔离目录。
- `llm-topic-brief` 新增 `--workspace-id`，直接从该目录读取
  `run.json`、`materials/current.json` 和当前 generation 的
  `materials.jsonl`。
- 冻结 generation、Card 投影 SHA、三个来源文件 SHA、Card 数和文件大小。
- 执行前重新计算全部冻结字段；任一变化直接失败。

这修复了全局 corpus 中同名论文重复时，旧分析器无法唯一选择论文的问题。

### Job 与发布

- Job 账本支持 `card_build` 和 `topic_brief` 两种固定类型。
- 同一时刻仍只允许一个外部任务。
- 每篇论文使用独立 `llm-topic-brief` run，不跨论文拼接 Prompt。
- Job 保存 run ID、模型状态、请求数、Token、Evidence 数和失败代码。
- CLI、manifest、paper brief、paper ID、主题、generation 和输入 SHA
  全部一致才视为成功。
- 本批所选论文的旧分析索引一次性替换；失败论文不继续显示旧分析。
- 发布分析 run 后项目 revision 加一，并清除旧综合 run。

### 界面

- “运行 Card”和“分析论文”是两个独立入口。
- 未完成 Card 的论文不能启动分析。
- 分析确认框显示论文、Card 数、模型、Non-think、generation 和输入 SHA。
- 未勾选 DeepSeek 外发确认时，前端按钮禁用，后端也拒绝创建 Job。
- 项目页显示最近分析 Job、逐篇结果、Evidence 数、Token 和完整日志。

## 真实运行

- project：`m2-card-debug-20260728`
- UI Job：`brief-20260728T084718-f8785d53`
- 分析 run：`ui-brief-20260728T084718-f076be36-001`
- 论文：`三峡永久船闸水力学问题研究`
- Card generation：`20260728T080614151350Z-8dcc1247`
- 模型：`deepseek-v4-pro`
- thinking：disabled
- 状态：completed
- 相关性：core
- 来源 Card：1
- 选中 Card：1
- Evidence：1
- 请求：3
- Prompt Token：5,677
- Completion Token：700
- 总 Token：6,377
- 失败：0
- Job 运行时间：约 16.3 秒
- 项目 revision：2 → 3

该运行由项目页选择论文、查看预检、勾选外发确认并点击“确认并运行”产生，
不是直接调用 CLI。

## 验收

- 全量 288 个 unittest 通过。
- 新增测试覆盖同名论文 corpus 冲突、外发确认门禁、分析 run 发布和
  ArtifactStore 逻辑身份映射。
- Python `compileall` 通过。
- 前端 TypeScript 和 Vite 生产构建通过。
- 浏览器验证项目页、分析确认框、运行状态和论文 Evidence 详情均可读取。
- 本地服务使用项目独立虚拟环境运行：
  `C:\Users\NING\Desktop\Python\CURSOR\shipping\.venv`。

## 已暴露边界

调试论文的 MinerU Markdown 后半段混入其他文章。当前 Card generation 的范围选择
只发布目标论文的 1 张 Card，M2.3 只消费该冻结 Card，因此本次 Evidence 未读取
后续混杂原文。该现象属于上游 PDF/Markdown 解析质量，不在 M2.3 中增加兜底。

## 下一批

M2.4 接入跨论文综合：

1. 显式展示并选择每篇论文的分析 run。
2. 冻结 run manifest 与 synthesis collection。
3. 明确确认后运行现有 `llm-topic-synthesis`。
4. 对账综合 manifest、输入 run、主题、请求和 Token。
5. 成功后原子写回项目 `synthesis_run_id`。
