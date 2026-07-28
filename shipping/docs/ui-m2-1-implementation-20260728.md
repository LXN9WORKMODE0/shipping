# 可视化工作台 M2.1 实施记录

日期：2026-07-28

## 定性结论

M2.1 已完成。工作台不再只是读取预先写好的演示配置，现在能够创建综述任务、
修改任务元数据、批量导入来源文件，并为每次论文集合变化发布不可变 collection。
本批没有执行 MinerU、Card 或 LLM，也没有改写历史运行产物。

## 已实现链路

```text
React 表单
  -> FastAPI projects router
     -> ProjectService
        -> ProjectRepository
           -> workspace/_ui/projects/<project_id>/
```

- Router 只处理 HTTP/Pydantic/multipart 参数。
- Service 负责整批来源校验、流式落盘、哈希和用例编排。
- Repository 负责项目 revision、路径约束、原子 JSON 和 collection 快照。
- ArtifactStore 继续只读 Card、Evidence、失败账本和综合产物。

## 状态合同

项目使用 `review_ui_project.v2`，保存：

- 稳定 `project_id`、名称、主题和说明。
- 乐观并发 `revision`。
- 每篇论文的受管或外部来源、原文件名、大小和 SHA-256。
- 当前不可变 collection 路径。
- 显式单篇分析 run 和综合 run 引用。

collection 使用 `review_pipeline_collection.v1`。文件名由规范化内容 SHA-256
生成；论文新增或主题变化时生成新快照。无变化保存不推进 revision。

来源导入采用整批提交：任一格式、空文件、论文 ID 或内容哈希冲突都会拒绝整批，
不会发布部分 project 状态。支持 `.pdf`、`.md`、`.markdown` 和 `.txt`。

## 迁移

`adversarial-8papers-20260728` 已迁移到 v2：

- revision：1
- 论文：8
- Card：957
- Evidence：31
- 当前 collection：`collections/collection-ca35e4aa384a22bb.json`

原论文仍作为外部来源引用；历史 Card、单篇分析和综合 run 未改写。

## 验收

- 前端生产构建通过。
- Python `compileall` 通过。
- 全量 284 个 unittest 通过。
- HTTP 测试覆盖新建、multipart 导入、空项目读取和 revision 冲突。
- 浏览器验证覆盖任务列表、迁移项目、新建窗口、导入窗口。
- 390px 移动端无页面横向溢出，表格在自身容器内滚动。
- 浏览器应用日志无前端 error/warning。

## 明确边界

- M2.1 不运行 pipeline，导入后的论文显示为 `not_run`。
- 不删除论文、不删除项目；治理动作留到 M3.3。
- `运行记录` 仍主要展示既有样本运行，项目级 job 视图由 M2.2 接管。
- 没有自动重试、模型切换或失败跳过。

## 下一批入口

M2.2 从当前 `current_collection_path` 和 project revision 创建 Card job。需要新增：

1. `job_repository.py`：不可变 job 输入和可变状态账本。
2. `command_factory.py`：只允许构造既有 Card pipeline 命令。
3. `services/job_service.py`：单任务队列、状态迁移和 manifest 对账。
4. `api/jobs.py`：预检、创建、查看日志和状态。
5. 前端论文选择、Card 预检确认、运行记录和失败定位。

M2.2 不接入 DeepSeek；外部发送确认从 M2.3 开始。
