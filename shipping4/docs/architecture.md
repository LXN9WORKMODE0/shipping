# Architecture

## Runtime layers

- `src/core`: environment settings, shared models, runtime paths, logging.
- `src/clients`: MinerU and LLM adapters. These modules only call remote services.
- `src/workspace`: unified paper workspace repository under `workspace/<paper_id>/`.
- `src/parsing`: markdown parsing, front matter detection, heading rules, structure building.
- `src/pipeline`: use cases for convert, parse, summarize.
- `src/cli`: Click bootstrap, command registration, terminal formatting.

## Data flow

1. `convert` calls MinerU and stores raw files in `workspace/<paper_id>/raw/mineru/`.
2. Cleaned and normalized markdown is saved to `workspace/<paper_id>/normalized/document.md`.
3. `parse` builds `workspace/<paper_id>/structure/structure.json`.
4. Top-level sections are exported to `workspace/<paper_id>/sections/`.
5. `summarize` reads `structure.json` plus `normalized/document.md` content slices referenced by `content_ref`.
6. Phase 4 summary artifacts are written to `workspace/<paper_id>/summaries/summary_tree.json`, `overview.json`, and `review_notes.json`.

## Current parsing scope

- First-stage heuristics support Chinese thesis and journal markdown that already has heading markers.
- `structure.json` is the only upstream structure artifact used for structural decisions.
- `summary_tree.json`, `overview.json`, and `review_notes.json` are all derived artifacts and must not infer structure from `sections/`.
- LLM refinement is restricted to low-confidence nodes; summarization uses the persisted structure plus `content_ref` slices.

## Execution Notes

- Phase history and current project status are tracked in `docs/execution-roadmap.md` and `docs/CHANGELOG.md`.
