# Codex working scope

The active project is `shipping/`. Treat `shipping1/` through `shipping4/` as
legacy implementations unless the user explicitly names one of them.

For routine code changes:

- Search the smallest relevant subtree first, normally
  `shipping/src/shipping_pipeline/`, `shipping/src/shipping_ui/`, or the matching
  test file under `shipping/tests/`.
- Do not scan `shipping/workspace/`, generated run artifacts, historical progress
  journals, or `shipping/docs/superpowers/` unless the task explicitly concerns
  runtime evidence or historical design decisions.
- Read targeted symbols or line ranges in large files before reading whole files.
  In particular, avoid loading all of `llm_analysis.py`, `main.py`, `App.tsx`, or
  their large test modules when a narrower search is sufficient.
- Run the narrowest relevant tests first. Expand to broader suites only when the
  change crosses module boundaries or the focused tests reveal a dependency.
- Preserve external LLM behavior and model token settings unless the user
  explicitly requests changes to them.

Primary entry points:

- CLI: `shipping/main.py`
- Pipeline code: `shipping/src/shipping_pipeline/`
- UI service: `shipping/src/shipping_ui/`
- Frontend: `shipping/ui/frontend/src/`
- Tests: `shipping/tests/`

