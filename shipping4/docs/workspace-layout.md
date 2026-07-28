# Workspace Layout

Each paper has an isolated workspace:

```text
workspace/<paper_id>/
├── raw/
│   └── mineru/
│       ├── full.md
│       └── manifest.json
├── normalized/
│   └── document.md
├── structure/
│   ├── structure.json
│   ├── diagnostics.json
│   ├── outline.json
│   └── refinement.json
├── sections/
│   └── 00_<title>.md
├── summaries/
│   ├── summary_tree.json
│   ├── overview.json
│   └── review_notes.json
└── run.json
```

## File roles

- `raw/mineru/`: the original MinerU extraction output.
- `normalized/document.md`: the canonical markdown used by parsing and summarization.
- `structure/structure.json`: semantic structure tree with roles, semantic levels, numbering paths, low-confidence flags, and content references.
- `structure/diagnostics.json`: parse diagnostics and low-confidence evidence.
- `structure/outline.json`: flattened outline derived from `structure.json`.
- `structure/refinement.json`: optional LLM refinement audit trail for low-confidence nodes.
- `sections/`: top-level section exports derived from `structure.json`.
- `summaries/summary_tree.json`: hierarchical summary tree derived from the structure.
- `summaries/overview.json`: quick-scan overview for the whole paper.
- `summaries/review_notes.json`: structured notes for downstream literature review writing.
- `run.json`: processing status and artifact index for the paper.
