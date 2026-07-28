# Changelog

All notable changes to this project will be documented in this file.

## [Phase 4] - 2026-04-13

### Added

- Rebuilt `GenerateSummaryUseCase` so it produces a phase4 summary bundle from `structure.json` and `content_ref` slices: `summary_tree.json`, `overview.json`, and `review_notes.json`.
- Added overview and review-note persistence APIs in `WorkspaceRepository`, plus run-record indexing via `summary_stage = "phase4_complete"`.
- Added CLI commands `view-overview` and `view-review-notes`, and updated `summarize` to announce the full phase4 artifact set.
- Added regression coverage proving summary generation does not depend on exported `sections/` files and that all three summary artifacts round-trip correctly.

## [Phase 3 Patch] - 2026-04-13

### Fixed

- Rejected LLM `role` changes that would require reparenting or child-tree rebuilds, so refinement no longer creates mixed-role subtrees that violate the structure model.
- Recomputed `is_low_confidence`, `low_confidence_node_ids`, `node_type`, and summary stats after refinement merges, so accepted LLM fixes are reflected in `structure.json` and `diagnostics.json`.
- Persisted refinement fallback warnings into diagnostics and `run.json`, and now mark fallback runs as `parse_stage = "phase3_fallback"` with an explicit `refinement_status`.
- Added regression coverage for unsafe `role` changes, stale low-confidence flags, and failed LLM refinement fallback persistence.

## [Phase 2 Patch] - 2026-04-13

### Fixed

- Corrected `ordinal` assignment to follow real sibling order under the same parent, so `structure.json` and `outline.json` can reconstruct node order without guessing from role groupings.
- Promoted rule-level risk evidence such as `schema_conflict` and `level_jump` into low-confidence diagnostics, and now append `low_confidence_threshold` evidence when threshold-based low confidence is triggered.
- Fixed `ConvertPaperUseCase` so it reloads `run.json` after parse and no longer overwrites `parse_stage`, `diagnostics`, or `outline` artifacts produced by the parse pipeline.
- Added regression coverage for sibling ordinal ordering, rule-risk diagnostics inclusion, and `run.json` persistence after `convert -> parse`.

## [Phase 1 Patch] - 2026-04-13

### Fixed

- Wired `clean_noise_lines()` into `normalize_markdown()` so the real convert/parse path now uses the same cleaned markdown baseline.
- Added regression tests for noisy markdown normalization and for persisted `workspace/<paper_id>/normalized/document.md`.
- Closed the Phase 1 review gap where helper code existed but was not connected to the parsing pipeline.

## [Phase 1] - 2026-04-13

### Completed

**目标**：把中文期刊论文和中文学位论文的结构识别做到稳定可用。

### A. `src/parsing/markdown.py` - 规范化与噪声处理

- **`normalize_markdown()`** - 增强规范化步骤：
  - 统一换行符（`\r\n` → `\n`）
  - 去掉行尾多余空格
  - 合并连续空白行到固定上限（`MAX_BLANK_LINES = 2`）

- **新增 `_collapse_blank_lines()`** - 将连续空白行合并为固定上限

- **新增 `clean_noise_lines()`** - 清除以下噪声：
  - 页码行（如 `----- 1 -----` 或纯数字行）
  - 图片占位符（`![alt](url)`、`图1...`、`Table 1...`）
  - 纯符号/分隔线标题
  - 重复的页眉页脚行

- **新增 `_is_noise_line()`, `_is_page_number_line()`, `_is_image_placeholder()`, `_is_empty_or_symbol_title()`, `_is_duplicate_header_footer()`** - 噪声检测辅助函数

- **`parse_markdown_headings()`** - 调整：
  - 新增 `allow_no_space_numbering` 参数（默认 `True`）
  - 支持识别"无空格编号标题"，如 `1.1.1研究背景`

- **新增 `_match_no_space_heading()`** - 匹配无空格编号标题，返回 `(title, level)` 元组

### B. `src/parsing/headings.py` - 编号体系识别重写

- **`detect_schema()`** - 重写为统计密度判断：
  - 遍历整篇文档统计三种编号体系候选密度
  - 引入 `SCHEMA_MIN_DENSITY = 0.15` 阈值
  - 不再依赖单个标题决定体系

- **`parse_heading_info()`** - 调整：
  - 所有分支统一使用 `_normalize_title()` 处理标题
  - 修复 chapter 正则 `[:：\s\-]*` 改为 `[:：\-]*`，避免吞掉标题空格

- **新增 `_normalize_title()`** - 标题归一化：
  - 调用 `_remove_page_number_suffix()` 去页码尾巴
  - 合并多余空格
  - 调用 `_unspace_chinese_words()` 处理中文分词空格

- **新增 `_remove_page_number_suffix()`** - 去掉 `........ 1` 类页码后缀

- **新增 `_unspace_chinese_words()`** - 统一中文词空格：
  - `摘 要` → `摘要`
  - `ABSTRACT` → `ABSTRACT`
  - `参 考 文 献` → `参考文献`
  - `目 录` → `目录`
  - `致 谢` → `致谢`

- **新增 `_compute_low_confidence()`** - 计算无编号标题的置信度（中文标题 0.6，其他 0.4）

- **新增 `is_level_jump()`** - 检测编号层级跳跃（如 level 1 → level 3 跳过 2）

### C. `src/parsing/front_matter.py` - 区域状态机

- **新增 `DocArea` 枚举** - 明确支持的区域状态：
  - `UNKNOWN`, `TITLE_PAGE`, `DECLARATION`, `AUTHORIZATION`
  - `CN_ABSTRACT`, `EN_ABSTRACT`, `TOC`, `BODY`
  - `REFERENCES`, `APPENDIX`, `ACK`

- **新增 `detect_area()`** - 状态机核心函数，根据标题和内容判断文档区域

- **新增辅助检测函数**：
  - `_is_title_page()` - 检测学校名、论文类型标题
  - `_is_declaration()` - 检测原创性声明
  - `_is_authorization()` - 检测授权书
  - `_is_cn_abstract()` - 检测中文摘要
  - `_is_en_abstract()` - 检测英文摘要
  - `_is_body_start()` - 判断正文起始（需要同时满足主编号体系候选或正文关键词）

- **新增正则模式**：
  - `SCHOOL_PATTERNS` - 学校/学院/研究所
  - `TITLE_PAGE_PATTERNS` - 学位论文标题
  - `DECLARATION_PATTERNS` - 原创性声明
  - `AUTH_PATTERNS` - 授权书

### D. `src/parsing/structure_builder.py` - 分类和建树逻辑

- **`_classify_candidates()`** - 重写：
  - 用 `DocArea` 状态机替代 `before_body` 布尔标志
  - 只有在 `current_area != DocArea.BODY` 且满足 `is_likely_body_start()` 时才切换到 body
  - 正文起始后才设置 `current_area = DocArea.BODY`
  - TOC 合并逻辑只在目录状态下生效（`DocArea.TOC`）
  - 参考文献/附录/致谢识别前置于任何正文判断

- **`_make_node()`** - 调整：
  - 使用 `_normalize_title()` 的结果填充 `title_norm`

- **`_build_stats()`** - 增强：
  - 增加 `body_node_count` - 正文节点计数
  - 增加 `toc_node_count` - 目录节点计数
  - `role_counts` 保持各 role 类型计数

### E. `src/pipeline/parse.py` - 运行时校验

- **新增 `ParseError` 异常类** - 无标题文档或解析失败的明确错误

- **`execute()`** - 增加运行时校验：
  - 无标题文档抛出 `ParseError`
  - 解析成功但 `body` 节点为空时记录 `_parse_warning` 警告
  - `run.json` 增加 `parse_stage = "phase1_complete"` 字段

- **新增 `_flatten_nodes()`** - 递归扁平化节点树（辅助校验）

### F. 测试

- **新增 `tests/fixtures/thesis_complex.md`** - 复杂中文学位论文样例：
  - 包含标题页、声明页、授权书
  - 含大量噪声 TOC（`........ 1` 页码）
  - 包含完整正文章节（5个章节能正常识别）

- **`tests/test_heading_rules.py`** - 新增测试用例：
  - `test_parse_heading_info_handles_bracket_decimal` - `（1）` 样式编号
  - `test_parse_heading_info_normalizes_spaced_abstract` - `摘 要` 归一化
  - `test_parse_heading_info_normalizes_spaced_references` - `参 考 文 献` 归一化
  - `test_parse_heading_info_removes_page_number_suffix` - 页码后缀移除

- **`tests/test_structure_builder.py`** - 新增测试用例：
  - `test_structure_builder_handles_complex_thesis_with_noise` - 复杂学位论文（含噪声 TOC、前置页）
  - `test_structure_builder_stats_include_role_counts` - stats 字段完整性验证

- **`tests/conftest.py`** - 新增 `thesis_complex_markdown` fixture

### 测试结果

```
============================= 17 passed ==============================
tests/test_cli.py::test_check_command_runs PASSED
tests/test_cli.py::test_status_and_convert_commands_run PASSED
tests/test_cli.py::test_batch_url_aliases_work PASSED
tests/test_heading_rules.py::test_detect_schema_prefers_chapter_decimal PASSED
tests/test_heading_rules.py::test_parse_heading_info_handles_thesis_numbering PASSED
tests/test_heading_rules.py::test_parse_heading_info_handles_chinese_enumeration PASSED
tests/test_heading_rules.py::test_parse_heading_info_handles_bracket_decimal PASSED
tests/test_heading_rules.py::test_parse_heading_info_normalizes_spaced_abstract PASSED
tests/test_heading_rules.py::test_parse_heading_info_normalizes_spaced_references PASSED
tests/test_heading_rules.py::test_parse_heading_info_removes_page_number_suffix PASSED
tests/test_pipeline.py::test_convert_parse_summarize_pipeline PASSED
tests/test_structure_builder.py::test_structure_builder_handles_thesis_front_matter PASSED
tests/test_structure_builder.py::test_structure_builder_handles_journal_numbering PASSED
tests/test_structure_builder.py::test_structure_builder_handles_complex_thesis_with_noise PASSED
tests/test_structure_builder.py::test_structure_builder_stats_include_role_counts PASSED
tests/test_workspace_repository.py::test_workspace_repository_prepares_expected_layout PASSED
tests/test_workspace_repository.py::test_workspace_repository_round_trips_run_record_and_structure PASSED
```

### Phase 1 完成标准验收

- [x] 现有学位论文样例中：标题页、声明页、授权书不会被当作正文章节
- [x] 现有学位论文样例中：目录被合并为 `toc` 节点，不会进入正文树
- [x] `摘要/ABSTRACT/参考文献/附录/致谢` 角色正确
- [x] 现有期刊样例中：`一、 -> （一） -> 1.` 的层级关系正确
- [x] `sections/` 中不再出现明显由目录或前置页导出的伪章节
- [x] 所有测试通过

## [Phase 2 Task Package 01] - 2026-04-13

### Completed

**目标**：把 `structure.json` 升级为"可调试、可解释、可复用"的核心语义层，并生成 `diagnostics.json` 和 `outline.json` 配套诊断产物。

### A. `src/core/models.py` - 数据模型扩展

**`SectionNode`** - 新增字段：
- `parent_id: str | None` - 父节点 ID，根节点为 `None`
- `node_type: str` - 节点类型，`section | container | special`
- `ordinal: int` - 同级顺序，从 1 开始
- `is_low_confidence: bool` - 是否为低置信度节点
- `evidence: list[str]` - 规则证据短语列表
- `llm_reviewed: bool` - 是否经过 LLM 审核

**`DocumentStructure`** - 新增字段：
- `diagnostics_version: str` - 诊断版本号（默认 `"1.0"`）
- `low_confidence_node_ids: list[str]` - 低置信度节点 ID 列表

**新增 `LowConfidenceNode`** - 诊断报告中的低置信度节点记录：
- `node_id`, `title_raw`, `role`, `semantic_level`, `confidence`, `evidence`

**新增 `DiagnosticsReport`** - 诊断报告模型：
- `paper_id`, `detected_schema`, `generated_at`
- `low_confidence_nodes: list[LowConfidenceNode]`
- `schema_evidence: list[str]`, `warnings: list[str]`

**新增 `OutlineNode`** - 轻量大纲节点：
- `id`, `title`, `role`, `semantic_level`, `ordinal`, `is_low_confidence`, `parent_id`

### B. `src/workspace/repository.py` - 产物持久化扩展

新增接口：
- `save_diagnostics(paper_id, diagnostics: DiagnosticsReport) -> Path`
- `load_diagnostics(paper_id) -> DiagnosticsReport | None`
- `save_outline(paper_id, outline: list[OutlineNode]) -> Path`
- `load_outline(paper_id) -> list[OutlineNode] | None`

### C. `src/parsing/structure_builder.py` - 结构字段补齐

**`build()`** - 调整：
- 调用 `_assign_parent_ids()` 填充父节点关系
- 调用 `_assign_ordinals()` 填充同级序号
- 调用 `_assign_node_types()` 填充节点类型
- 调用 `_detect_low_confidence_nodes()` 标记低置信度节点
- `DocumentStructure` 新增 `low_confidence_node_ids` 字段

**新增 `build_diagnostics()`** - 根据结构生成诊断报告

**新增 `build_outline()`** - 根据结构生成轻量大纲

**新增 `_assign_parent_ids()`** - 递归设置所有子节点的 `parent_id`

**新增 `_assign_ordinals()`** - 按层级和角色分组设置 `ordinal`

**新增 `_assign_node_types()`** - 根据 role 和 children 递归设置 `node_type`：
- `front_matter/toc/references/appendix/ack` → `special`
- 有子节点的 body → `container`
- 无子节点 → `section`

**新增 `_detect_low_confidence_nodes()`** - 置信度低于阈值（0.7）时标记 `is_low_confidence` 并补充证据

**`_classify_candidates()`** - 新增证据收集：
- `plain_heading` - 无编号标题
- `schema_conflict` - 编号体系冲突
- `level_jump` - 层级跳跃
- `front_matter_boundary` - 前置页边界

### D. `src/pipeline/parse.py` - 产物接线

**`execute()`** - 调整：
- 调用 `builder.build_diagnostics()` 生成诊断报告
- 调用 `builder.build_outline()` 生成大纲
- 保存 `diagnostics.json` 和 `outline.json`
- `run.json` 更新 `parse_stage = "phase2_complete"`
- 新增 `_build_schema_evidence()` 辅助生成 schema 证据

### E. 测试

**`tests/test_workspace_repository.py`** - 新增测试：
- `test_workspace_repository_diagnostics_round_trip` - diagnostics 持久化 round-trip
- `test_workspace_repository_outline_round_trip` - outline 持久化 round-trip
- 更新 `test_workspace_repository_round_trips_run_record_and_structure` 覆盖新字段

**`tests/test_structure_builder.py`** - 新增测试：
- `test_structure_builder_assigns_parent_ids_and_ordinals` - 父子关系和序号验证
- `test_structure_builder_node_type_assignment` - node_type 正确性验证
- `test_structure_builder_low_confidence_detection` - 低置信度检测
- `test_structure_builder_build_diagnostics` - 诊断报告生成
- `test_structure_builder_build_outline` - 大纲生成

**`tests/test_pipeline.py`** - 新增测试：
- `test_parse_produces_diagnostics_and_outline` - 集成验证 diagnostics/outline 落盘

### 测试结果

```
============================= 27 passed ==============================
tests/test_cli.py::test_check_command_runs PASSED
tests/test_cli.py::test_status_and_convert_commands_run PASSED
tests/test_cli.py::test_batch_url_aliases_work PASSED
tests/test_heading_rules.py::test_detect_schema_prefers_chapter_decimal PASSED
tests/test_heading_rules.py::test_parse_heading_info_handles_thesis_numbering PASSED
tests/test_heading_rules.py::test_parse_heading_info_handles_chinese_enumeration PASSED
tests/test_heading_rules.py::test_parse_heading_info_handles_bracket_decimal PASSED
tests/test_heading_rules.py::test_parse_heading_info_normalizes_spaced_abstract PASSED
tests/test_heading_rules.py::test_parse_heading_info_normalizes_spaced_references PASSED
tests/test_heading_rules.py::test_parse_heading_info_removes_page_number_suffix PASSED
tests/test_markdown.py::test_normalize_markdown_removes_noise_lines PASSED
tests/test_markdown.py::test_convert_pipeline_persists_cleaned_markdown PASSED
tests/test_pipeline.py::test_convert_parse_summarize_pipeline PASSED
tests/test_pipeline.py::test_parse_produces_diagnostics_and_outline PASSED
tests/test_structure_builder.py::test_structure_builder_handles_thesis_front_matter PASSED
tests/test_structure_builder.py::test_structure_builder_handles_journal_numbering PASSED
tests/test_structure_builder.py::test_structure_builder_handles_complex_thesis_with_noise PASSED
tests/test_structure_builder.py::test_structure_builder_stats_include_role_counts PASSED
tests/test_structure_builder.py::test_structure_builder_assigns_parent_ids_and_ordinals PASSED
tests/test_structure_builder.py::test_structure_builder_node_type_assignment PASSED
tests/test_structure_builder.py::test_structure_builder_low_confidence_detection PASSED
tests/test_structure_builder.py::test_structure_builder_build_diagnostics PASSED
tests/test_structure_builder.py::test_structure_builder_build_outline PASSED
tests/test_workspace_repository.py::test_workspace_repository_prepares_expected_layout PASSED
tests/test_workspace_repository.py::test_workspace_repository_round_trips_run_record_and_structure PASSED
tests/test_workspace_repository.py::test_workspace_repository_diagnostics_round_trip PASSED
tests/test_workspace_repository.py::test_workspace_repository_outline_round_trip PASSED
```

### Phase 2 Task Package 01 验收标准

- [x] `structure.json` 能单独承担后续结构消费，不需要额外猜 parent/ordinal
- [x] 任意低置信度节点都能在 `diagnostics.json` 中找到证据
- [x] `outline.json` 是 `structure.json` 的派生产物，不是另一套独立结构
- [x] 本轮改动后不存在只在内存里有、但磁盘产物没有的关键结构字段
- [x] 所有新增规则都有测试覆盖
- [x] 老的 `convert -> parse -> summarize` stub 集成测试仍通过
