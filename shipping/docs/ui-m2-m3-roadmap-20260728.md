# 可视化工作台 M2/M3 分批实施路线

日期：2026-07-28

## 目标

在 M1 只读工作台基础上逐步增加写入和运行能力，同时保持现有 pipeline 是唯一业务实现，
界面不复制解析、Evidence 合同或综合规则。

固定前提：

- 本地单用户。
- 文件系统是事实来源，不引入数据库。
- 每次运行输入显式冻结。
- 同一时刻只允许一个外部 API 任务。
- 不自动重试、换模型或复用旧代际；批处理允许部分成功，但必须逐论文记录结果。

## 分层结构

```text
API Router
  -> Application Service
     -> Repository / Job Manager
        -> 现有 shipping_pipeline 与不可变 workspace 产物
```

- `API Router`：HTTP 参数、状态码和外部发送确认。
- `Application Service`：用例编排，不直接拼接任意命令。
- `Repository`：项目、collection 和 job 账本的原子读写。
- `Job Manager`：固定命令工厂、单任务队列、进程和日志。
- `Artifact Store`：继续只读 Card、Evidence 和综合产物。

## 批次

### M2.1 项目生命周期

状态：**已完成并通过批次屏障（2026-07-28）**。

- `workspace/_ui/projects/<project_id>/project.json` 成为项目事实来源。
- 新建任务、修改元数据、批量导入 PDF/Markdown。
- 来源文件复制到项目目录并记录 SHA-256。
- 论文清单生成不可变 collection 版本。
- 迁移现有 8 篇任务，不改写历史 Card 或分析 run。

本批不执行 pipeline，也不调用外部服务。

实施与验收记录见 `docs/ui-m2-1-implementation-20260728.md`。

### M2.2 PDF/Markdown → Card

状态：**已完成并通过批次屏障（2026-07-28）**。

- 固定命令工厂只生成已有 `main.py run` 参数。
- 一个 job 对应一批显式论文和 collection SHA。
- 单任务队列、stdout/stderr 日志、manifest 对账。
- 前端选择论文后独立运行 Card 阶段。
- 上游失败时不触发单篇分析。

实施与真实运行记录见 `docs/ui-m2-2-implementation-20260728.md`。

### M2.3 单篇主题分析

状态：**已完成并通过批次屏障（2026-07-28）**。

- 运行前冻结每篇 Card generation。
- 显示将发送给 DeepSeek 的论文、Card 数和模型 profile。
- 用户明确确认外发后才创建 job。
- 逐篇独立运行；状态继续区分完全、部分 Evidence 失败和回查未闭合。

实施与真实 DeepSeek 运行记录见
`docs/ui-m2-3-implementation-20260728.md`。

### M2.4 跨论文综合

状态：**已完成并通过批次屏障（2026-07-28）**。

- 显式选择每篇单篇 run。
- 不隐式选择“最新成功”。
- 冻结 synthesis collection 后运行。
- 主题矩阵、提纲和覆盖审计回到现有 M1 结果页。

实施、失败暴露和真实 8 篇综合记录见
`docs/ui-m2-4-implementation-20260728.md`。

### M3.1 完整流程和运行比较

状态：**已完成并通过真实 8 篇完整流程验收（2026-07-28）**。

- 完整流程只作为次级入口。
- 展示同一项目不同 run 的输入、状态、成本和覆盖差异。
- 不允许把不同 generation 的结果误当成同一输入比较。

实施、无外部屏障验证和待决策项见
`docs/ui-m3-1-implementation-20260728.md`。

### M3.2 项目内论文池解耦

状态：**已完成并通过浏览器验收（2026-07-28）**。

- 来源导入逐文件返回成功、重复跳过和失败，不再整批拒绝。
- Card 和单篇分析 Job 支持 `completed_with_failures`。
- 失败归属到具体论文，成功论文可继续进入下游。
- 增加已制卡选择、处理失败筛选和严格完整流程标识。
- 综合继续从显式可用分析 run 冻结输入。

实施与验收记录见 `docs/ui-m3-2-paper-pool-20260728.md`。

### M3.3 协作式取消和恢复

状态：**已完成并通过浏览器验收（2026-07-29）**。

- Job 先记录取消意图，再终止当前子进程并在论文边界收束。
- 已完成子运行保留，顶层 job 标记 `cancelled`。
- 服务重启后依据 Job 账本中已原子记录的逐论文结果对账。
- 不自动恢复；用户显式创建新 run。
- 界面可选择最近一次逐论文 Job 中未成功的论文重新运行。

状态机合同和实施验收记录见
`docs/job-control-contract-20260729.md` 与
`docs/ui-m3-3-job-control-20260729.md`。

### M3.4 运行治理

状态：**下一实施批次**。

- 模型、Token、耗时和失败率统计。
- 项目归档与只读历史。
- 配置健康检查。
- M2/M3 验收报告和界面回归。

## 目录目标

```text
src/shipping_ui/
  api/
    projects.py
    jobs.py
    readonly.py
  services/
    project_service.py
    job_service.py
  project_repository.py
  job_repository.py
  command_factory.py
  artifact_store.py
  errors.py
```

避免为每个小对象建立目录；只有 API、用例、持久化和执行四类边界。

## 批次屏障

每一批必须同时满足：

1. 新状态有固定 schema 和原子发布。
2. API 不接受任意命令或任意文件系统路径。
3. 真实 8 篇任务保持可读。
4. 单元测试、全量测试、前端构建和浏览器验收通过。
5. 实施记录明确已完成范围和下一批输入。

不跨批隐藏未解决问题。
