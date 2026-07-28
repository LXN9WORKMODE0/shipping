# 可视化工作台 M2.2 实施记录

日期：2026-07-28

## 结论

M2.2 已成功运行。工作台现在可以选择项目内论文，预检冻结输入，创建单任务 Card
Job，实时查看进度和日志，并在成功后直接读取新 generation 的 Card。

本批只执行 PDF/Markdown 到 Card。没有调用 DeepSeek，也不会自动触发单篇分析。

## 执行结构

```text
api/jobs.py
  -> services/job_service.py
     -> job_repository.py
     -> command_factory.py
        -> main.py run
           -> LiteraturePipeline
```

- `review_ui_card_job_input.v1` 冻结项目 revision、collection SHA-256、
  论文来源 SHA-256、执行顺序和受控命令。
- `review_ui_job.v1` 保存 queued/running/completed/failed 状态、逐篇结果和失败代码。
- Job 和日志位于 `workspace/_ui/jobs/<job_id>/`。
- 执行器只有一个工作线程；存在 queued/running Job 时拒绝创建另一个。
- 服务重启发现未完成 Job 时标记 `ui.job_interrupted`，不自动恢复。
- 命令通过 argv 启动，不使用 shell，也不接受任意命令或任意路径。

## 身份隔离

发现原 pipeline 同时把 `paper_id` 用作论文语义身份和磁盘目录名，多个项目的同名论文
可能冲突。M2.2 将两者拆开：

- `paper_id`：用户可读论文身份，继续用于标题匹配和 Card 内容。
- `workspace_paper_id`：带项目命名空间的内部目录 ID。
- CLI 新增可选 `--workspace-id`，只改变磁盘目录，不改变论文身份。

迁移项目没有该字段时仍使用原 `paper_id`，所以历史路径和运行不变。

## 对账条件

子进程退出码为零仍不足以判定成功。Job 必须同时验证：

1. CLI JSON 的论文身份和状态。
2. `workspace/<workspace_paper_id>/run.json` 为 completed 且有 generation。
3. `materials/current.json` 指向同一 completed generation。
4. 发布的材料卡数量大于零。

任何条件失败均记录明确失败代码。

## 真实运行

保留的调试项目：

- project：`m2-card-debug-20260728`
- 来源：`三峡永久船闸水力学问题研究` 的真实 MinerU Markdown
- UI Job：`card-20260728T080613-319176ad`
- 状态：completed
- generation：`20260728T080614151350Z-8dcc1247`
- 结构质量：silver
- Card：1
- Block：5
- 结构问题：2

该结果通过页面多选、预检和“创建并运行”按钮产生，不是直接调用 pipeline。

## 验收

- 全量 287 个 unittest 通过。
- HTTP 集成测试真实启动子进程并生成 Card。
- 前端 TypeScript 生产构建通过。
- Python compileall 通过。
- 桌面端和 390px 移动端无页面横向溢出。
- 运行记录页可查看全部 Card Job；项目页显示最近 Job、逐篇结果和完整日志。
- 浏览器应用日志无 error/warning。

## 下一批

M2.3 将以 completed Card generation 为唯一输入：

1. 冻结每篇 `workspace_paper_id`、逻辑 `paper_id` 和 generation。
2. 运行前展示 Card 数、模型 profile、预计外发范围。
3. 用户确认后逐篇执行现有 `llm-topic-brief`。
4. Job 账本记录每篇 run ID、状态、Token 和失败代码。
5. 完成后显式写回项目的 `analysis_run_ids`。
