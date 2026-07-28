# 可视化工作台 M1 实施记录

日期：2026-07-28

## 实施范围

完成 `docs/ui-design-v1-20260728.md` 中的 M1 只读工作台：

- 不调用 MinerU、DeepSeek 或其他外部服务。
- 不修改 Card generation、单篇分析 run 或跨论文综合 run。
- 通过显式 `review_ui_project.v1` 配置绑定论文清单、单篇 run 清单和综合 run。
- 按 `schema_version` 读取现有产物；未知版本不做宽松解析。

## 新增结构

```text
config/ui/
  adversarial-8papers-20260728.json
src/shipping_ui/
  artifact_store.py
  app.py
  __main__.py
scripts/
  run_ui.py
ui/frontend/
  src/
  package.json
  pnpm-lock.yaml
requirements-ui.txt
```

后端使用 FastAPI，前端使用 React、TypeScript、Vite、TanStack Query 和 Lucide。
生产构建由 FastAPI 在同一地址提供。

## 已实现页面

1. 综述任务列表。
2. 8 篇论文工作台和状态筛选。
3. 原始 Markdown 与 Card、Evidence、失败账本的行号联动。
4. 三层覆盖和结构问题。
5. 主题矩阵、综合单元、章节提纲和覆盖审计。
6. 当前主题的单篇、综合和完整流程运行历史。
7. 只读系统状态。

## 真实数据验收

项目：`adversarial-8papers-20260728`。

- 8 篇论文。
- 957 张 Card。
- 31 条 Evidence。
- 4 篇 `completed`。
- 3 篇 `completed_with_failures`。
- 1 篇 `completed_with_revisit_failure`。
- 7 个主题、11 个综合单元、7 个章节。
- 2 条多主题 Evidence。
- 1 条未归组 Evidence。
- 8 条已归组但未进入提纲 Evidence。

论文联动样本：

- Evidence “路线选择模型不考虑船舶总重量、公路限高等限制因素……”自动定位
  `normalized/document.md L128-L135`。
- 失败账本首项自动定位 `L9-L9`，并展示
  `citation.evidence_claim_numeric_fact_unsupported` 和冻结 Card 全文。

## 浏览器验收

- 1440×900：任务表、论文双栏和主题矩阵无重叠。
- 390×844：导航、阶段状态和指标转为单列；论文表在局部横向滚动，
  页面本身没有横向溢出。
- 分析状态筛选得到 3 篇 `completed_with_failures`。
- 覆盖审计显示 31/32/28/2/1/22/8 等真实统计，并展开 1 条未归组和
  8 条未入纲 Evidence。
- 修正了论文详情从加载态切换到数据态时的 React Hook 顺序错误。
- 修正了项目清单的工程内路径边界和 `requests` 数组计数。
- 最终任务、论文详情和综合页面控制台无 error/warn。

## 启动

```powershell
..\.venv\Scripts\python.exe scripts\run_ui.py --host 127.0.0.1 --port 8765
```

地址：`http://127.0.0.1:8765/`。

## 后续边界

M2 才增加新建任务、导入论文、分阶段运行和外部发送确认。M1 的 API 全部为 GET，
不得通过界面触发 pipeline。
