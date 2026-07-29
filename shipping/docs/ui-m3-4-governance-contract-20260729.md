# 可视化工作台 M3.4 运行治理合同

日期：2026-07-29

## 目标

在已有 Job 账本、不可变运行记录和项目论文池之上，补齐日常运行所需的三个治理入口：

1. 汇总工作台任务的请求、Token、耗时和失败情况。
2. 检查本地 Pipeline、模型与外部服务配置是否完整。
3. 将暂时不用的项目归档为只读状态，并可显式恢复。

本批不修改 Markdown、Card、Evidence 或综合规则，不引入数据库、账号权限、自动清理或外部监控。

## 统计边界

治理总量只统计 `workspace/_ui/jobs` 中由工作台创建的 Job。

底层 `_topic_reviews`、`_topic_syntheses` 和 `_pipeline_runs` 仍显示在历史运行列表中，但不再次计入治理总量，避免同一次外部调用在顶层 Job 与底层 run 中重复计算。

统计字段：

- Job 总数、活动数和终态数。
- 请求数。
- prompt、completion 和 total Token。
- 已结束 Job 的累计耗时。
- 成功、失败、取消和中断数量。
- 已处理论文的成功数、失败数及论文失败率。
- 按 Job 类型和模型分组。
- 失败代码频次。

取消时尚未开始的论文不进入失败率分母。没有模型的 Card Job 只进入阶段统计，不进入模型统计。

## 配置健康

配置健康检查只读取本机文件和环境配置，不访问 MinerU 或 LLM 网络端点。

检查项：

- workspace 存在且可写。
- Pipeline 入口存在。
- `.env` 存在且语法可解析。
- LLM URL 与 API Key 已配置。
- MinerU URL 与 API Key 已配置。
- 默认模型 profile 是有效 JSON。
- 单篇分析与跨论文综合配置存在。
- 当前 Python 可执行文件存在。

接口只返回“已配置/缺失/无效”和非敏感说明，不返回 URL、API Key 或 env 原值。

## 项目归档

归档是项目定义上的逻辑状态：

```text
active -> archived -> active
```

- 归档与恢复都要求匹配当前 project revision，并推进 revision。
- 有活动 Job 的项目不能归档。
- 归档项目保留来源、Card、Evidence、综合和 Job 记录。
- 归档项目可继续只读浏览。
- 归档后禁止修改元数据、导入来源、冻结 collection 和创建新 Job。
- 恢复后重新开放原有写入与运行入口。
- 默认项目列表只显示活动项目，用户可切换查看归档项目。

归档不移动目录、不删除文件、不重写历史运行，也不自动取消活动任务。

## API

```text
GET  /api/governance/summary
GET  /api/projects?include_archived=true
POST /api/projects/{project_id}/archive
POST /api/projects/{project_id}/restore
```

归档与恢复请求体：

```json
{
  "expected_revision": 3
}
```

## 验收

1. 治理统计不会把 UI Job 和底层 run 重复计数。
2. Token 分项、请求、耗时、论文失败率和分组结果可由测试账本确定性复算。
3. 配置健康不返回任何密钥值，也不产生网络请求。
4. 活动 Job 阻止归档。
5. 归档项目拒绝所有写入和新 Job，但历史产物仍可读取。
6. 恢复后可重新修改和运行。
7. 前端可以查看治理汇总、配置检查、活动/归档项目并执行归档与恢复。
