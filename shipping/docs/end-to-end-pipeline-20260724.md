# 整套文献综述 Pipeline 实施记录

日期：2026-07-24

## 1. 目标

把已经分别实现的阶段串成一个显式、可审计、不可覆盖的总入口：

```text
论文来源清单
  -> 每篇 PDF/Markdown 转换与制卡
  -> 单篇主题简报与严格 Evidence
  -> 排除无关论文
  -> 跨论文主题矩阵和段落级提纲
```

总入口不调用可选的完整论文深度审计。默认单篇分析仍是轻量 `llm-topic-brief`，符合“综述服务于主题，不为所有论文做全面拆解”的既定方向。

## 2. 显式输入

输入使用 `review_pipeline_collection.v1`：

```json
{
  "schema_version": "review_pipeline_collection.v1",
  "topic": "综述主题",
  "papers": [
    {
      "paper_id": "稳定论文ID",
      "source": "相对collection文件或绝对来源路径"
    }
  ]
}
```

至少两篇论文。`paper_id` 和来源路径均不得重复；程序不扫描目录、不猜测最新文件，也不从文件名隐式决定论文集合。

## 3. 阶段屏障

### Card 屏障

同一阶段会处理清单中的全部论文以集中暴露问题。任一论文没有完成 PDF/MD 到 Card：

- 顶层运行失败。
- 不调用任何 LLM。
- 不使用旧 Card 或成功论文子集继续。

### 单篇简报屏障

全部 Card 成功后，逐篇执行不可变 `llm-topic-brief` 子运行：

- `completed` 和 `completed_with_revisit_failure` 可进入综合。
- `excluded` 是合法相关性结论，不进入综合。
- 任一 `failed` 或未知状态阻断跨论文综合。
- 排除后少于两篇时明确失败。

### 跨论文综合屏障

程序根据本次顶层运行产生的有效简报 run ID 生成显式 synthesis collection。只有 `llm-topic-synthesis` 完成时，顶层才发布正式 `pipeline_result.json`。

## 4. 运行和产物

命令：

```powershell
.\.venv\Scripts\python.exe shipping/main.py full-pipeline --workspace shipping/workspace --collection shipping/config/pilots/full-pipeline-md-2papers-20260724.json --run-id 自定义运行ID --provider openai-compatible --model-profile shipping/config/models/deepseek-v4-pro-official.json --review-config shipping/config/topic-review-default.json --synthesis-config shipping/config/topic-synthesis-default.json --timeout 900
```

PDF 清单额外传入 `--pdf-provider mineru`。MinerU 精确模式继续读取现有 `.env` 和转换器配置。

顶层目录：

```text
workspace/_pipeline_runs/<run_id>/
  input/collection.json
  input/topic_synthesis_collection.json
  manifest.json
  review/pipeline_summary.md
  output/pipeline_result.json
```

每篇制卡、每篇简报和跨论文综合开始或结束后，顶层 `running` manifest 都会刷新。正式输出只在全部阶段通过后原子发布；失败运行只保留输入快照、阶段记录和中文摘要。

来源文件、collection、model profile、单篇配置和综合配置均记录路径及 SHA-256。Card generation、子运行 ID、排除状态、失败代码和最终报告路径进入顶层 manifest。

## 5. 当前边界

- v1 顺序处理论文，不并行调用 MinerU 或 LLM。
- 不自动重试、换模型、跳过失败论文或复用历史单篇简报。
- 不自动生成最终综述正文；当前终点仍是可追溯的主题矩阵和段落级提纲。
- 首轮真实试跑使用两篇现存 Markdown，先验证整套调度；PDF/MinerU 将使用同一入口另行验证。

## 6. 首轮真实试跑

### 第一次运行

`full-pipeline-md-2papers-20260724`：

- 两篇 Card 均成功。
- 第一篇主题简报成功。
- 第二篇把论文判为 `supporting`，却按 `core` 上限选择了 6 张 Card，超过允许的 4 张。
- 顶层在单篇简报屏障失败，没有调用跨论文综合。

据此把各相关性级别对应的 `selected_materials minItems/maxItems` 写入模型动态 Schema；响应后的独立硬校验继续保留，没有截断模型选择。

### 第二次运行

`full-pipeline-md-2papers-v2-20260724` 端到端完成：

- 两篇 Markdown 到 Card 均完成，结构质量均为 `silver`。
- 两篇均纳入综合，共形成 8 条严格 Evidence。
- 跨论文阶段生成 3 个主题、9 个综合单元、5 个章节。
- 8 条 Evidence 全部进入提纲。
- 形成 2 个跨论文综合单元。
- 没有未入纲综合单元；有 1 个缺口未列为优先检索方向。
- 总耗时约 169 秒，共 18 次 LLM 请求、246665 Token。
- 第二篇包含 250 张 Card，其单篇简报消耗 227798 Token，是本次成本和时延的主要来源；跨论文综合本身为 9206 Token。

第二篇状态为 `completed_with_revisit_failure`。基础简报和 6 条 Evidence 有效；自动回查新 Evidence 已抽取，但最终简报仅因使用合法量化内容对应的 `point_type=data` 未在旧枚举中而失败，部分回查结果没有并入正式简报。合同随后正式增加 `data` 类型和中文“数据”标签；已完成运行保持不可变，不改写历史状态。

当前代码会在新运行的顶层 manifest 和正式结果中汇总全部单篇简报与跨论文综合的 `request_count`、输入 Token、输出 Token 和总 Token。该功能在第二次真实试跑结束后补入；由于运行目录不可变，历史运行 `full-pipeline-md-2papers-v2-20260724` 没有回填这些顶层字段，上述总量由其子运行账本汇总得到。MinerU 调用信息仍保留在各论文 Card 运行的转换 metadata 中，不混入 LLM Token。

## 7. 验证记录

- 端到端调度、主题分析与综合相关测试：30 项通过。
- 全量测试：254 项通过。
- `compileall`：通过。
- `git diff --check`：通过。
