# PDF 论文处理工具

这个项目用于把中文期刊论文和学位论文从 PDF 转成统一工作区产物，并支撑后续的结构解析、章节导出、低置信节点修正和渐进式披露摘要。

## 当前目录结构

```text
shipping4/
├── main.py
├── papers/
├── workspace/
├── docs/
├── tests/
└── src/
    ├── core/
    ├── clients/
    ├── workspace/
    ├── parsing/
    ├── pipeline/
    └── cli/
```

## 工作流

1. `convert` 保存 MinerU 原始结果和规范化 Markdown。
2. `parse` 生成 `structure.json`、`diagnostics.json`、`outline.json` 和章节导出。
3. `summarize` 基于 `structure.json` 与 `content_ref` 生成 `summary_tree.json`、`overview.json`、`review_notes.json`。

## CLI

```bash
python main.py convert papers/论文.pdf
python main.py convert-url "https://example.com/paper.pdf"
python main.py batch
python main.py batch-url urls.txt
python main.py parse "论文标题"
python main.py summarize "论文标题"
python main.py view-summary "论文标题"
python main.py view-overview "论文标题"
python main.py view-review-notes "论文标题"
python main.py status
python main.py check
```

`process` 当前仍保留为 `convert` 的别名，`batch-urls` 保留为 `batch-url` 的兼容别名。

## 运行产物

所有产物统一保存在：

```text
workspace/<paper_id>/
```

详细说明见：

- [Project Status](docs/project-status.md)
- [Architecture](docs/architecture.md)
- [Workspace Layout](docs/workspace-layout.md)
- [Execution Roadmap](docs/execution-roadmap.md)

## 测试

```bash
pytest
```
