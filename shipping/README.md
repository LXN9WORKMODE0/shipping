# 通用文献材料 Pipeline

这个目录是新通用 pipeline 的隔离版本，独立于 `shipping4/`，也不复用 `shipping4` 的运行状态。

## 目标

把论文来源转换成显式、可追溯、可审核的综述准备材料。系统不假设每篇论文都能被完美解析；如果结构质量弱，就记录问题，并输出低置信度的证据卡。

## 当前输入范围

第一版支持：

- Markdown：`.md`、`.markdown`
- 纯文本：`.txt`
- PDF：`.pdf`，仅在显式启用 `--pdf-provider mineru` 时支持。

## 五个层次

1. `convert`
   - 把受支持的文本来源复制到 `workspace/<paper_id>/normalized/document.md`。
   - 不支持的格式会明确失败，不做静默转换。

2. `parse`
   - 识别章级标题，例如 `第一章 绪论`、`第 2 章 方法`、`一、前言`。
   - 写出 `structure/structure.json`。
   - 写出 `structure/quality.json`，质量标签为 `gold`、`silver` 或 `red`。

3. `material`
   - 对结构清晰和弱结构论文都输出 `material.v2` 证据卡。
   - 保留 `content_ref` / `source_span`，可以追溯回 `normalized/document.md` 的行号。
   - 输出 `extract`、`lead_excerpt` 和 `quality_flags`，不把机械截取的开头句伪装成完整摘要。
   - 长章节会切成有边界的材料卡；弱结构会记录 confidence flag，不伪造章节树。

4. `corpus`
   - 重建：
     - `workspace/_corpus/papers.jsonl`
     - `workspace/_corpus/materials.jsonl`
     - `workspace/_corpus/issues.jsonl`

5. `review_pack`
   - 构建 `workspace/_review_packs/<topic-slug>.json`。
   - 包含证据矩阵、清洗后的正文片段和问题列表，供后续 LLM 或人工流程使用。

## CLI

项目使用仓库根目录的独立 Python 环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r shipping\requirements.txt
```

默认从 `shipping/.env` 加载 API 配置。把 `shipping/.env.example` 复制为 `shipping/.env`，只填写需要测试的 API：

```dotenv
MINERU_API_URL=https://mineru.net
MINERU_API_KEY=
MINERU_MODEL_VERSION=vlm
MINERU_LANGUAGE=ch
MINERU_ENABLE_TABLE=true
MINERU_ENABLE_FORMULA=true
MINERU_IS_OCR=false
MINERU_MAX_WAIT_SECONDS=1800
MINERU_POLL_INTERVAL_SECONDS=5
LLM_ANALYSIS_API_URL=
LLM_ANALYSIS_API_KEY=
```

处理单篇 Markdown：

```powershell
python shipping/main.py run path/to/paper.md --workspace shipping/workspace --paper-id paper-001 --topic "研究 方法"
```

## M1/M2.1 可视化工作台

工作台读取现有 Card、单篇分析、失败账本和跨论文综合产物。M2.1 支持新建任务、
修改元数据、批量导入来源以及冻结 collection；M2.2 已支持从项目页选择论文并运行
PDF/Markdown 到 Card。PDF Job 会在确认后调用 MinerU，当前仍不从界面调用 LLM。

在现有独立环境中安装界面依赖并构建前端：

```powershell
.\.venv\Scripts\python.exe -m pip install -r shipping\requirements-ui.txt
cd shipping\ui\frontend
pnpm install
pnpm run build
cd ..\..
```

启动：

```powershell
..\.venv\Scripts\python.exe scripts\run_ui.py --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。项目事实来源位于
`workspace/_ui/projects/<project_id>/project.json`，旧 `config/ui/` 项目保持只读，
可用 `scripts/migrate_ui_project.py --project-id <id>` 显式迁移。未知 schema、
缺失产物、revision 冲突和越界路径会明确返回错误。

Card Job 的冻结输入、状态和日志位于：

```text
workspace/_ui/jobs/<job_id>/
  input.json
  job.json
  logs/job.log
```

项目页显示最近 Job；“运行记录”页显示全部历史 Card Job。调试实例为
`m2-card-debug-20260728`。

命令会打印 JSON 结果：

```json
{"paper_id":"paper-001","status":"completed","structure_quality":"gold","issue_count":0,"workspace_path":"shipping/workspace/paper-001"}
```

通过 MinerU 处理 PDF 后再进入正常 pipeline：

```powershell
python shipping/main.py run path/to/paper.pdf --workspace shipping/workspace --paper-id paper-001 --topic "研究 方法" --pdf-provider mineru
```

MinerU 默认使用精确解析 API：

- `POST /api/v4/file-urls/batch` 申请一个签名上传 URL。
- 用 `PUT` 把本地 PDF 上传到签名 URL。
- 轮询 `GET /api/v4/extract-results/batch/{batch_id}`，直到 `state=done`。
- 下载 `full_zip_url`，读取其中的 `full.md` 作为标准化 Markdown 来源。

默认模型为 `MINERU_MODEL_VERSION=vlm`。

如果没有传入 `--pdf-provider mineru`，PDF 输入会明确以 `convert.unsupported_input` 失败。

审计已有 normalized Markdown workspace：

```powershell
python shipping/main.py audit shipping4/workspace --workspace shipping/workspace --topic "研究 方法 结论" --run-id real-md-20260703
```

该命令只读取来源目录下的 `*/normalized/document.md`，并写出：

- `shipping/workspace/_audit/<run_id>/results.json`
- `shipping/workspace/_audit/<run_id>/report.md`

审计已生成材料卡：

```powershell
python shipping/main.py material-audit --workspace shipping/workspace --run-id material-quality-20260703
```

该命令读取 `shipping/workspace/_corpus/materials.jsonl`，并写出：

- `shipping/workspace/_audit/<run_id>/material-quality.json`
- `shipping/workspace/_audit/<run_id>/material-quality.md`

Markdown 审计报告采用结论优先的中文格式，便于人工阅读；JSON 文件保留机器可读的问题明细。

导出人工审核材料：

```powershell
python shipping/main.py material-review --workspace shipping/workspace --run-id material-v2-human-review-20260707
```

该命令读取 `shipping/workspace/_corpus/materials.jsonl`，并写出：

- `shipping/workspace/_human_review/<run_id>/index.md`
- `shipping/workspace/_human_review/<run_id>/papers/paper_001.md`
- `shipping/workspace/_human_review/<run_id>/manifest.json`

人工审核包的组织方式是：一个索引文件、每篇论文一个 Markdown 文件、每张材料卡带有勾选状态和备注区。

安装并校验固定版本的 DeepSeek-V4 tokenizer（首次执行需要访问 Hugging Face）：

```powershell
.\.venv\Scripts\python.exe shipping/scripts/setup_llm_tokenizer.py --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json
```

对全部当前 Card 生成只读 Token 规划审计，不调用 API：

```powershell
.\.venv\Scripts\python.exe shipping/scripts/validate_llm_plan_corpus.py --workspace shipping/workspace --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json
```

报告写入 `shipping/workspace/_llm_analysis/planning_audits/<run_id>/report.json` 和 `report.md`。非 `completed` 论文、无法规划论文和 Card 覆盖异常都会显式进入问题账本。

## 整套 Pipeline

使用显式论文清单一次串联 PDF/Markdown、Card、单篇主题简报和跨论文综合：

```json
{
  "schema_version": "review_pipeline_collection.v1",
  "topic": "三峡船舶积压疏导策略与长江航运组织",
  "papers": [
    {
      "paper_id": "论文ID",
      "source": "path/to/paper.pdf"
    }
  ]
}
```

```powershell
.\.venv\Scripts\python.exe shipping/main.py full-pipeline --workspace shipping/workspace --collection "collection.json" --run-id "整套运行ID" --provider openai-compatible --model-profile shipping/config/models/deepseek-v4-pro-official.json --review-config shipping/config/topic-review-default.json --synthesis-config shipping/config/topic-synthesis-default.json --pdf-provider mineru --timeout 900
```

Markdown 清单不需要 `--pdf-provider mineru`。同一阶段会处理全部论文以暴露问题，但任一制卡失败时不调用 LLM，任一应纳入论文的简报失败时不使用不完整子集执行跨论文综合。相关性判定为 `excluded` 是合法结果；排除后少于两篇时明确失败。

顶层运行位于 `workspace/_pipeline_runs/<run_id>/`。默认阅读 `review/pipeline_summary.md`；只有全部阶段完成才发布 `output/pipeline_result.json`。设计和状态合同见 `docs/end-to-end-pipeline-20260724.md`。

## 主题驱动论文材料简报（默认入口）

大多数论文不需要完整拆解。默认使用 `llm-topic-brief`：模型先读取同篇论文的全部精简 Card，围绕综述主题只选择少量真正有用的 Card；严格 Evidence 抽取只作用于这些入选 Card。初版简报形成后，核心/支持论文会按结构化证据缺口自动回查一次未入选 Card；边缘论文默认不回查。没有人工裁决环节。

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-topic-brief --workspace shipping/workspace --paper-id "论文ID" --topic "综述主题" --run-id "运行ID" --provider openai-compatible --model-profile shipping/config/models/deepseek-v4-pro-official.json --review-config shipping/config/topic-review-default.json --timeout 900
```

每篇论文经历以下处理阶段；Evidence 阶段按入选 Card 逐张调用，因此 API 请求数不是固定值：

1. `topic_scope`：判为核心、支持、边缘或排除，并只选择配置上限内的 Card。
2. `selected_evidence`：每张入选 Card 单独调用模型并只抽取1条最强主题证据，继续执行逐字引文、数值、限定词和表格合同，避免跨 Card 引文错配。
3. `base_topic_brief`：只读取已验证 Evidence，生成初版简报和最多2个结构化证据缺口。导航栏目禁止出现阿拉伯数字；主要观点的 Evidence ID 与准确 claim 由动态 Schema 直接绑定。
4. `automatic_revisit`：核心/支持论文存在证据缺口或首次选卡达到上限时，扫描同篇论文全部未入选 Card，最多补选2张。允许明确返回 `no_candidate`；边缘/排除论文默认跳过。
5. `final_topic_brief`：补选 Card 逐张通过严格 Evidence 合同后，重新生成简报。补选 Evidence 必须进入最终主要观点并占用原有观点名额，不能无限追加；回查只执行一轮。

报告会把定界、用途等导航性归纳与逐条引用原文的证据观点明确区分；写综述时应以证据观点及原文为准。

结果写入 `shipping/workspace/_topic_reviews/runs/<run_id>/`。默认阅读 `review/paper_brief.md`，其中会直接显示自动回查状态、补选原因、观点原文和 Markdown 行号。`exclude` 论文只执行第一阶段。首次未入选集合位于 `audit/initial_omitted_materials.jsonl`，回查后的最终未入选集合位于 `audit/omitted_materials.jsonl`。

自动回查状态为 `not_triggered/no_candidate/completed/failed`。回查失败时运行状态为 `completed_with_revisit_failure`：基础 Evidence 和 `brief/base/validated_brief.json` 保持可用，回查错误完整记录，但任何部分补选结果都不会并入正式简报。CLI 对该显式部分状态返回成功，使批量流程可以继续处理其他论文；下游必须读取状态，不能把它当成回查已完成。

## 跨论文主题综合

多篇论文完成 `llm-topic-brief` 后，使用 `llm-topic-synthesis` 生成跨论文主题矩阵、综合单元和段落级综述提纲。该阶段不重新解析 Markdown，也不重新抽取单篇 Evidence；它会读取单篇运行冻结的 Card 来重放校验 Evidence 引文，但不会把 Card 全文发送给跨论文模型，也不直接生成综述正文。

输入必须是显式 collection，不能让程序根据目录时间猜测同一论文的“最新运行”：

```json
{
  "schema_version": "llm.topic_synthesis_collection.v1",
  "topic": "三峡船舶积压疏导策略与长江航运组织",
  "source_run_ids": [
    "topic-brief-run-a",
    "topic-brief-run-b"
  ]
}
```

只接受当前 `llm.topic_review_run.v2`、`llm.topic_paper_result.v2` 和 `llm.topic_review_config.v2`。同一论文只能选择一个运行；`completed_with_revisit_failure` 可以进入，但会作为部分状态写入报告。旧 v1、失败、排除、主题不一致和 Evidence 引用损坏的运行直接拒绝。

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-topic-synthesis --workspace shipping/workspace --collection "collection.json" --run-id "综合运行ID" --provider openai-compatible --model-profile shipping/config/models/deepseek-v4-pro-official.json --synthesis-config shipping/config/topic-synthesis-default.json --timeout 900
```

固定执行两次全局 LLM 调用：

1. `theme_map`：每条来源 Evidence 必须且只能进入一个主主题，或者明确列为未归组；关系类型按独立论文数验证。
2. `outline`：把主题矩阵组织为跨论文综合单元、章节和段落任务。除单来源背景和语料缺口外，综合单元必须引用至少两篇论文；每个主题必须进入综合单元和章节。检索方向只选择值得继续追查的缺口，未优先缺口和未入纲候选单元会保留在覆盖审计中。

当前主题矩阵和提纲使用 v2 合同。模型只填写主题、综合单元和段落动作等语义草案；程序按独立论文数生成单来源类型，并由段落引用的综合单元生成章节 `theme_ids`、段落角色和 `evidence_unit_ids`。原始响应不会被覆盖，派生账本位于 `audit/contract_derivations.json`。设计与历史回放记录见 `docs/topic-synthesis-contract-v2-20260724.md`。

### 参考文献目录

`reference-catalog` 是独立于 Card、单篇分析和跨论文综合的确定性阶段。它只处理实际进入指定综合运行的论文，从本地 Markdown 提取题名、作者、DOI、文章编号、年期页码和学位信息；Markdown 缺失的字段只能由带字段级来源的显式核验表补充。

```powershell
.\.venv\Scripts\python.exe shipping/main.py reference-catalog `
  --workspace shipping/workspace `
  --project shipping/workspace/_ui/projects/review-20260728/project.json `
  --synthesis-run-dir shipping/workspace/_topic_syntheses/runs/综合运行ID `
  --overrides shipping/config/references/核验表.json `
  --review-draft shipping/workspace/_review_drafts/runs/草稿运行ID/review/review_draft.md `
  --run-id 参考文献运行ID
```

每次运行写入不可变目录 `workspace/_reference_catalogs/runs/<run_id>/`，主要产物包括：

- `output/references.json`：结构化题录及逐字段来源链。
- `output/references.bib`：BibTeX。
- `review/references.md`：人工可读题录审核表。
- `audit/incomplete_references.jsonl`：不完整题录；零行表示基本引用字段闭合。
- `review/review_draft_with_references.md`：仅在提供 `--review-draft` 时生成。程序精确匹配正文中的“证据来源”题名，连续编号并只附加正文实际引用的文献；无法映射时直接失败。

“完整”只表示当前文献类型所需的基本引用字段齐备，不表示来源均为一级权威记录，也不表示正文观点已通过事实核验。

覆盖审计同时给出跨论文主题数和跨论文综合单元数。结构合同通过但没有形成跨论文综合单元时，结果保留并标记 `cross_paper_synthesis_absent`；该标记用于下游判断综合强度，不通过自动重试消除。

当前 DeepSeek 模型使用一百万 Token 上下文，程序按 Evidence 数和主题数计算输出预算，不按字符拆批。超出上下文、任一请求失败或合同不成立时，运行状态为 `failed`，保留请求和中间审计，但不发布正式 output；只有 `completed` manifest 才表示 JSON 和中文报告已经共同发布。

当前官方 DeepSeek 供应商默认使用 `config/models/deepseek-v4-pro-official.json`。五篇真实 A/B 中 Flash 约快三倍、便宜三倍，但通过合同的主题矩阵没有形成跨论文主题，Pro 的跨论文组织质量更好，因此本阶段暂不切换到 Flash。对比记录见 `docs/deepseek-v4-flash-pro-comparison-20260724.md`。

结果写入 `shipping/workspace/_topic_syntheses/runs/<run_id>/`。默认阅读 `review/topic_synthesis.md`：主题关系和段落任务下方会直接展开论文标题、Evidence claim、逐字原文、Card 标题和 Markdown 行号，不需要人工根据 Evidence ID 跨文件查找。机器产物位于 `output/topic_synthesis.json`，覆盖账本位于 `audit/coverage.json`。

## 完整论文深度审计（可选入口）

下面的 `llm-analysis` 执行完整 Evidence 覆盖、综合和多道语义门禁，只用于确实需要高强度核验的核心论文，不再作为一般论文的默认处理方式。

对一篇论文执行 Card-to-LLM dry-run，不调用 API：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-analysis --workspace shipping/workspace --topic "三峡船舶积压与长江航运组织" --run-id llm-v3-dry-run --paper-id "慎防信息不对称影响船闸管理" --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --dry-run
```

这一阶段强制每次只处理一篇论文，并绑定该论文当前 Card generation。模型、上下文窗口、输入/输出预算、tokenizer revision 和 Non-think 模式只能来自已校验的 model profile。完整流程为：

1. 对每张 Card 作 `evidence/context/not_relevant/unusable` 判定。
2. 代码为 Card 全文生成无遗漏的精确引文候选；模型只选择 `quote_id`，不重新抄写 OCR 文本。规则表格中的复合数值事实必须同时引用表头和完整数据行。
3. 每张 Card 的判定和证据单元处于同一个嵌套结果中，由 Schema 排除“判为非证据却被引用”等矛盾状态。
4. 小中型论文整篇单批；超预算论文仅在论文内部按章节层级拆分。
5. 首次 Evidence 响应发生可精确定位的合同错误时，只对失败 `material_result` 执行一次 `llm.evidence_batch_correction.v1`；成功 Card 不重跑，第二次失败立即终止。表格列数错位、多行表头歧义或超长行拆分等不可证明结构不进入自动修正。
6. 证据抽取完成后，独立模型按一级章节、每批至多 `evidence_claim_batch_size` 个 Evidence 核验 `claim` 是否被逐字引文完整直接支持；首轮阻断项再以单条 Evidence、被引 Card 和相邻 Card 做一次窄上下文复核。
7. 通过 Evidence claim 门禁后生成带 evidence ID 引用的论文草稿；代码从引用集合确定 `used`，模型只分类未引用证据，避免重复状态漂移。
8. 代码组装最终论文分析后，先独立核验每条陈述是否履行所在字段职责，再核验陈述是否被其引用证据完整支持；任一门禁失败都阻断发布。
9. 两轮 claim 结论一致才形成确认阻断；结论冲突记为 `support.evidence_claim_gate_unstable`，停止运行但不自动升级人工。相同模型、prompt、输入和 tokenizer 的已验证响应按内容哈希复用。
10. 综合及三道语义核验都受 Token 预算约束；必要时先生成章节摘要，最后生成中文审核报告和带稳定证据 ID 的审核决策 CSV。

先执行最小 Non-think 路由能力探针：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-provider-probe --workspace shipping/workspace --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --timeout 300
```

探针必须返回 `finish_reason=stop`、零 reasoning 内容，并记录本地与服务端 prompt token。探针失败时不得继续真实论文调用。

使用 OpenAI-compatible chat completion API 分析单篇论文：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-analysis --workspace shipping/workspace --topic "三峡船舶积压与长江航运组织" --run-id llm-v3-api --provider openai-compatible --paper-id "慎防信息不对称影响船闸管理" --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --timeout 900
```

模型输出必须符合 `llm.evidence_batch.v4`、`llm.evidence_batch_correction.v1`、`llm.evidence_claim_support_review.v1`、`llm.section_summary.v2`、`llm.paper_analysis_draft.v2`、`llm.non_used_evidence_disposition.v1`、`llm.statement_role_review.v1` 和 `llm.statement_support_review.v1`；最终 `llm.paper_analysis.v4` 由代码合并并复核，发布包装为 `llm.paper_result.v3`。runner 不补模型必填字段、不修复非法 JSON、不自动重试 API。Evidence 修正是独立、最多一次的正式阶段：只允许处理能映射到具体 Card 和原 Evidence 索引的错误，原响应保持失败并通过 `resolved_by_request_id` 连接修正请求。`quote_id` 由代码解析为对应 Card 的逐字原文；表格裸数值与表头单位只有在同组、同列且列数一致时才能组成复合事实，百分比必须引用表头和完整数据行，同一单元格事实不得重复，排序事实必须覆盖全部比较行。无法证明表格结构时直接失败。研究焦点、方法、核心结论、局限、综述用途和未决问题中的每条自由文本都必须引用已有 evidence unit，全部证据必须有唯一最终去向。未被精简综合采用但仍相关的方法或数据细节使用 `peripheral/supporting_detail`，不得伪装成背景或重复证据。Evidence claim 核验器只读取 `claim`、逐字引文和 caveats；字段职责核验器不读取证据；陈述支持核验器只读取陈述及其已引用证据。一般分析字段要求直接支持，综述用途允许明确标记的有依据推论，不允许自动改写、移动、删除或降级失败项。

每个不可变运行写入：

- `shipping/workspace/_llm_analysis/runs/<run_id>/manifest.json`
- `input/`：论文、原始 Card、精简 Card 投影、精确引文候选和章节映射快照。
- `plan/`：model profile、tokenizer manifest、证据规划和综合规划。
- `schemas/`、`prompts/`：实际契约和系统提示词。
- `batches/`：每次请求、原始响应、解析结果、校验结果、计划/实际 token 和 `finish_reason`。
- `output/`：材料判定、证据单元、Evidence claim 首轮/复核/决策账本、字段职责核验、逐条陈述支持核验、门禁前不可变论文候选和通过全部门禁后的论文级分析。
- `_llm_analysis/evidence_claim_cache/`：经完整身份哈希校验的 Evidence claim 响应；缓存损坏或身份不一致会直接失败，不退回重新调用。
- `coverage.json`、`audit/`：Card/证据覆盖率、失败账本和 `evidence_corrections.jsonl` 修正链账本。
- `review/review.md`、`review/decisions.csv`：人工审核材料。

使用 `--limit` 的运行会标为 `partial_smoke`，只验证 API，不生成论文级分析。除一次性 Evidence 修正阶段外，失败批次不会自动重试；明确检查失败原因后，可以创建子运行：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-analysis --workspace shipping/workspace --topic "三峡船舶积压与长江航运组织" --run-id llm-v3-retry --provider openai-compatible --paper-id "慎防信息不对称影响船闸管理" --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --retry-from llm-v3-api --retry-batch-id evidence_具体请求ID
```

证据失败重跑要求 Card generation、输入、schema、prompt、provider 和 model 与父运行完全一致。直接综合、未采用证据分类或陈述支持核验失败时，只复用提示词哈希一致且重新通过当前合同校验的已完成上游阶段。陈述支持核验失败的子运行默认只重新调用该核验请求，不自动改写论文分析。若已审查确认需要重新生成分析，可显式把该失败运行中位于失败阶段之前的已完成 `paper_synthesis` 或 `evidence_disposition` 请求作为 `--retry-batch-id`；runner 会从指定阶段重新执行并使其下游结果失效，不允许对成功运行或失败阶段之后的批次这样操作。manifest 使用 `retry_mode=restart_from_completed_synthesis` 记录该动作，并把证据、综合、分类和核验请求全部计入执行/复用账本。

只读回放历史 Evidence 失败并验证一次性修正协议：

```powershell
.\.venv\Scripts\python.exe shipping/scripts/replay_evidence_corrections.py --workspace shipping/workspace --source-run-id 历史运行 --run-id 修正回放运行 --timeout 900
```

回放产物写入 `workspace/_llm_analysis/evidence_correction_replays/<run_id>/`，不会改写来源运行。开发校准时可显式传入 `--reuse-correction-run-id`，复用既有修正响应并按当前合同重新全量验证；验证不通过才发起新的唯一一次请求，复用本身不绕过任何 Schema 或内容门禁。

合同升级后，可扫描历史运行的全部 Evidence 响应，并只回放指定批次：

```powershell
.\.venv\Scripts\python.exe shipping/scripts/replay_evidence_corrections.py --workspace shipping/workspace --source-run-id 历史运行 --run-id 当前合同回放 --scan-current-contract --batch-id evidence_具体请求ID --timeout 900
```

只读审计当前 Card 语料中的表格复合证据能力，不调用 API：

```powershell
.\.venv\Scripts\python.exe shipping/scripts/audit_table_composite_evidence.py --materials-jsonl shipping/workspace/corpus/materials.jsonl --output-dir shipping/workspace/_audits/table-composite-自定义ID
```

只读使用当前 Evidence 合同重验一个历史 LLM 运行，不调用 API、不修改来源运行：

```powershell
.\.venv\Scripts\python.exe shipping/scripts/audit_evidence_run_contract.py --workspace shipping/workspace --source-run-id 历史运行 --output-dir shipping/workspace/_audits/evidence-contract-自定义ID
```

两类审计都生成中文 `report.md` 和机器可读结果。表格审计会完整列出被拒绝的表格文本；历史合同审计会展示失败 claim、所选逐字引文和错误代码。

若支持核验已经形成有效的阻断意见，可显式执行一次受约束修订：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-analysis-revise --workspace shipping/workspace --source-run-id llm-v4-nonthink-large82-atomic-support-retry3-20260715 --run-id llm-v4-large82-revision1 --provider openai-compatible --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --timeout 1800
```

修订器只接收被拒绝的 `statement_id`、原引用证据和核验意见，只允许删除、拆分或收窄陈述；replacement 不能增加 evidence ID。修订导致原 used evidence 失去全部引用时，模型必须显式给出合法的非采用处置，代码再重建完整 `paper_analysis.v4`。随后先核验全文字段职责，再核验全文证据支持，全部通过才写入 `output/paper_analysis.json`；失败时只保留 `output/revised_analysis_candidate.json` 和 `review/revision.md`。修订运行不能作为下一次修订来源，不形成自动或人工触发的模型修订循环。

若首轮修订已生成合法候选，但全文核验响应只因 Schema 或陈述覆盖不完整而失败，可以显式复用该候选并仅重跑一次核验：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-analysis-revise --workspace shipping/workspace --source-run-id 原始分析运行 --retry-support-from 首轮修订运行 --run-id 核验重试运行 --provider openai-compatible --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --timeout 1800
```

该入口只接受同一原始分析的首轮失败修订，且失败代码必须是 `schema.statement_support_invalid`；它不会再次调用修订模型。核验重试运行不能成为另一次核验重试来源。语义上的 `partially_supported/unsupported` 不是可重试错误，必须保持失败。

`llm.model_profile.v4` 分别配置 `prompt_token_overhead`、`large_paper_evidence_batch_max_cards`、`evidence_claim_batch_size`、`evidence_claim_output_base_tokens`、`evidence_claim_output_tokens_per_unit`、`evidence_claim_support_max_output_tokens`、`evidence_revision_max_output_tokens`、`statement_revision_max_output_tokens`、`statement_role_max_output_tokens` 与 `statement_support_max_output_tokens`。`prompt_token_overhead` 用于显式记录供应商 chat 模板的固定计费开销，仍要求计划 Token 与实际 Token 精确一致。缺少字段、超过上下文窗口或 tokenizer 不匹配时直接拒绝规划；小中型论文在整体预算内仍保持单批，大论文章节内的证据批次同时受 Card 身份数量上限和 token 预算约束，claim 小批输出预算按 Evidence 数量增长并受上限约束。

对既有显式修订候选执行只读语义门禁审计：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-semantic-audit --workspace shipping/workspace --source-run-id 已结束的修订运行 --run-id 语义审计运行 --provider openai-compatible --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --timeout 1800
```

该入口只接受当前 Card generation 对应的完整论文修订产物，同时执行 Evidence claim 和字段职责核验。即使一道门禁失败，另一道仍独立执行并写入同一份 `review/semantic_audit.md`。产物固定写入 `workspace/_llm_analysis/semantic_audits/<run_id>/`，不会改写来源，也不会生成 `output/paper_analysis.json`。

### 受约束语义自动处置

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-semantic-auto-resolve --workspace shipping/workspace --audit-run-id 语义审计、上一轮自动处置或普通字段职责失败运行 --run-id 新自动处置运行 --provider openai-compatible --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --timeout 1800
```

该入口只允许补充当前/相邻 Card 的精确引文、映射到同篇论文已通过门禁的重复 Evidence、对低风险等值改述保存签名绑定的自动覆盖、删除共享 Evidence 的错位重复陈述，或显式升级人工。普通分析在字段职责门禁前会保存 `paper_analysis_candidate.json` 及来源哈希，因此字段职责失败可直接接续，不需要先补做一轮语义审计。产物写入 `workspace/_llm_analysis/semantic_auto_resolutions/<run_id>/`；失败运行可作为下一轮输入，但不会改写来源。只有三道语义门禁全部通过才生成 `output/validated_paper_analysis.json`，其状态仍是独立候选，不进入下游语料。

语义审计存在阻断项且没有 API、Schema 或覆盖技术失败时，会同时生成：

- `review/semantic_decision_guide.md`：中文裁决说明。
- `review/semantic_decision_workbook.md`：按裁决项展开的自包含证据工作单，依次展示 MinerU Markdown/Card 原文快照、相邻 Card、Evidence 输出、论文级输出、门禁冲突和处置后果。
- `review/semantic_decisions.csv`：与工作单编号一致的人工裁决表；同样内嵌上述上下文，内部 ID 只用于程序绑定。

旧语义审计可显式初始化一次裁决表；已有文件不会被覆盖：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-semantic-decisions-init --workspace shipping/workspace --audit-run-id 语义审计运行
```

如果旧表仍全部为 `待裁决` 且没有人工说明，可显式升级为新版工作单；任何已填写的裁决都会阻止替换：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-semantic-decisions-init --workspace shipping/workspace --audit-run-id 语义审计运行 --replace-pending
```

人工可以只按“第1项、第2项”审核，无需检索 Evidence ID。只能修改 `人工裁决` 和 `人工说明` 两列，其余原文与过程列逐格参与防篡改校验。裁决必须是 `确认缺陷` 或 `门禁误判`，所有行均须填写说明。`确认缺陷` 会触发一次受约束处置：Evidence claim 只能在原逐字引文范围内收窄或删除，字段职责问题会作为重新综合时的禁止复现约束；`门禁误判` 不改内容，只生成与来源审计、候选哈希、稳定 ID、原 verdict 和失败片段绑定的显式覆盖。

完成裁决后执行：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-semantic-adjudicate --workspace shipping/workspace --audit-run-id 语义审计运行 --run-id 人工裁决运行 --provider openai-compatible --model-profile shipping/config/models/siliconflow-deepseek-v4-pro.json --timeout 1800
```

裁决执行是一次性不可变运行，不允许以裁决运行继续形成修订链。Evidence claim 发生变化时生成新 Evidence ID 并废弃旧论文候选；随后重新综合，并依次重跑 Evidence claim、字段职责和陈述支持门禁。人工确认错位的原陈述若以同一稳定 ID 再现，会在字段核验 API 前直接失败。人工误判覆盖只对身份、内容、verdict 和失败片段均未变化的同一问题有效；任何新问题仍阻断发布。全部通过后才生成 `llm.paper_result.v4`，并在 `semantic_adjudications` 中同时保留原模型结论、人工说明、实际处置和覆盖是否生效。

填写 `review/decisions.csv` 后导入审核状态：

```powershell
.\.venv\Scripts\python.exe shipping/main.py llm-review-import --workspace shipping/workspace --run-id llm-v3-api
```

## 测试

测试使用项目独立环境和 `jsonschema`：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s shipping/tests -v
```
