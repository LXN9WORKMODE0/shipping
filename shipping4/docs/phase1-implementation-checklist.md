# Phase 1 Implementation Checklist

本清单只覆盖 `Phase 1：解析质量打底`，目标是在现有新架构上，把中文期刊论文和中文学位论文的结构识别做到稳定可用。

## 1. Phase 1 目标

- 稳定区分：
  - `front_matter`
  - `toc`
  - `body`
  - `references`
  - `appendix`
  - `ack`
- 稳定识别三类主编号体系：
  - `第X章 -> 1.1 -> 1.1.1`
  - `一、 -> （一） -> 1.`
  - `1 -> 1.1 -> 1.1.1`
- 降低以下误判：
  - 目录条目被当作正文标题
  - 标题页/声明页/授权书被当作正文章节
  - `摘 要`、`ABSTRACT`、`参考文献`、`附录`、`致谢`角色错误
  - 一级章节误拆或错位

## 2. 本阶段不做的事

- 不接入 LLM 结构修正。
- 不改 `summary_tree.json` 的最终产品设计。
- 不扩展 `structure.json` 的复杂诊断字段体系。
- 不做综述卡片或 `review_notes.json`。

## 3. 代码实施清单

### A. `src/parsing/markdown.py`

- 增加规范化步骤：
  - 统一换行符。
  - 去掉行尾多余空格。
  - 合并连续空白行到固定上限。
- 增加噪声处理函数：
  - 去掉明显的页码行。
  - 去掉重复页眉页脚候选。
  - 去掉纯图片占位、空标题、仅符号标题。
- 调整 `parse_markdown_headings()`：
  - 保留原始标题文本。
  - 允许识别“无空格编号标题”，例如 `1.1.1研究背景`。
  - 对标题内容区间计算保持稳定。

### B. `src/parsing/front_matter.py`

- 把当前基于单点规则的判断扩展为“文档区域状态机”。
- 明确支持的区域状态：
  - `title_page`
  - `declaration`
  - `authorization`
  - `cn_abstract`
  - `en_abstract`
  - `toc`
  - `body`
  - `references`
  - `appendix`
  - `ack`
- 增加以下规则集：
  - 学校名、学位论文、Dissertation、原创性声明、授权书识别。
  - 中文摘要与英文摘要识别。
  - 目录标题识别。
  - 参考文献、附录、致谢识别。
- 增加目录项判定：
  - 标题带页码尾巴。
  - 内容主要由点线、页码、编号组成。
  - 标题形似正文，但出现在目录状态中。
- 增加正文起始判定：
  - 只有当标题同时满足“主编号体系候选”或“正文关键词”时，才允许切到 `body`。

### C. `src/parsing/headings.py`

- 重写 `detect_schema()`：
  - 先统计整篇文档中三种编号体系的候选密度。
  - 输出主编号体系，不靠单个标题决定。
- 重写 `parse_heading_info()`：
  - 支持 `第X章`。
  - 支持 `1.1`、`1.1.1`、`1.1.1.1`。
  - 支持 `一、`、`（一）`、`1.`、`（1）`、`①`。
  - 支持标题与编号之间无空格的写法。
- 增加标题归一化逻辑：
  - 去掉编号前缀。
  - 去掉目录页码尾巴。
  - 统一空格标题，如 `摘 要 -> 摘要`、`参 考 文 献 -> 参考文献`。
- 增加低置信度条件：
  - 标题没有编号但被当作正文。
  - 编号层级跳跃。
  - 标题看起来像目录条目但落在正文。
  - 编号体系与整篇文档主模式冲突。

### D. `src/parsing/structure_builder.py`

- 调整 `_classify_candidates()`：
  - 引入区域状态机，而不是仅靠 `before_body`。
  - `toc` 合并逻辑只在目录状态下生效。
  - 目录结束必须由正文起始条件触发。
- 调整 `_merge_toc_candidates()`：
  - 合并目录标题和后续目录项。
  - 不把正文章节误吞进目录。
- 调整 `_make_node()`：
  - 为 `title_norm` 使用新的标题归一化结果。
  - 让 `confidence` 真正反映规则判定质量。
- 调整 `_nest_nodes()`：
  - 只对 `body` 节点建树。
  - `references`、`appendix`、`ack` 始终作为顶层特殊节点。
- 调整 `stats`：
  - 增加各 `role` 节点计数。
  - 增加正文节点数和目录节点数，方便后续验收。

### E. `src/pipeline/parse.py`

- 保持接口不变，但补充运行时校验：
  - 无标题文档要给出明确错误。
  - 解析成功但 `body` 节点为空要标记异常。
- 在 `run.json` 中记录：
  - 当前解析阶段状态。
  - 生成的 `structure.json` 路径。
  - 生成的 `sections/` 路径。

## 4. 测试实施清单

### A. 扩充 fixtures

- 在 `tests/fixtures/` 新增或补充：
  - 1 个复杂中文学位论文样例
  - 1 个中文期刊样例
  - 1 个目录页噪声更重的样例

### B. 单元测试

- `tests/test_heading_rules.py`
  - `第X章`
  - `1.1`
  - `1.1.1`
  - `一、`
  - `（一）`
  - `1.`
  - `（1）`
  - `摘 要`
  - `参 考 文 献`
- `tests/test_structure_builder.py`
  - 学位论文前置页识别
  - 目录合并
  - 正文起始点识别
  - 参考文献/附录/致谢角色识别
  - 期刊中文编号层级识别

### C. 集成测试

- 保持 `convert -> parse` 主链路可跑。
- 校验生成的 `structure.json` 至少满足：
  - 存在 `body` 节点
  - `toc` 不为空时不会混入正文树
  - `sections/` 文件数与顶层正文节点数一致

## 5. 执行顺序

1. 先改 `markdown.py`，把输入清洗稳定下来。
2. 再改 `headings.py`，把编号识别稳定下来。
3. 再改 `front_matter.py`，把区域状态机建立起来。
4. 再改 `structure_builder.py`，把分类和建树逻辑切到新规则上。
5. 最后改 `parse.py` 和测试，补运行时校验和回归覆盖。

## 6. Phase 1 完成标准

- 现有学位论文样例中：
  - 标题页、声明页、授权书不会被当作正文章节。
  - 目录被合并为 `toc` 节点，不会进入正文树。
  - `摘要/ABSTRACT/参考文献/附录/致谢` 角色正确。
- 现有期刊样例中：
  - `一、 -> （一） -> 1.` 的层级关系正确。
- `sections/` 中不再出现明显由目录或前置页导出的伪章节。
- 所有测试通过。

## 7. 当前执行入口

进入执行时，直接以本清单为准，实施范围限定在：

- `src/parsing/markdown.py`
- `src/parsing/front_matter.py`
- `src/parsing/headings.py`
- `src/parsing/structure_builder.py`
- `src/pipeline/parse.py`
- `tests/fixtures/`
- `tests/test_heading_rules.py`
- `tests/test_structure_builder.py`
- 必要时补充 `tests/test_pipeline.py`
