# Progress against the requirements tracker

Status as of 2026-10-23. ✅ done · 🟨 partial · ⬜ not started · 🔍 written but not verified in this environment.

| Phase | Status | Notes |
| --- | --- | --- |
| P0 Foundations | 🟨 | Playwright not set up; CI and Docker written but not run |
| P1 Providers | ✅ | Not yet exercised against a live OpenRouter or Ollama endpoint |
| P2 Upload & profiling | ✅ | 1,067,371-row CSV: convert 0.6 s + profile 0.4 s |
| P3 AI generation | ✅ | Tested with a scripted fake LLM, including a repair round |
| P4 Review | ✅ | |
| P5 Registry & versions | ✅ | |
| P6 Query engine & tools | ✅ | 37 compiler cases matched against hand-written SQL |
| P7 Chat | 🟨 | Tested with a scripted streaming fake LLM; no live model tested yet |
| P8 Hardening | 🟨 | Usage log, debug payloads and query cache done; evals and E2E not started |
| P9 Extensibility | ⬜ | `relationships` reserved in the schema |
| P10 Analytics foundations | 🟨 | Analyses run as chat tools with LLM-safe summaries and a result cache; no plugin registry, job runner, derived dimensions or Analyses tab yet |
| P11 Segmentation | 🟨 | RFM scores and segments, K-means k = 2..10 by silhouette, cross-tab and ARI, `segment_customers` tool; cluster names are rule-based, not LLM-generated |
| P12 Forecasting | 🟨 | SARIMA with ADF/KPSS, STL seasonal strength, AIC search with drift, leakage-free backtest vs naive and seasonal naive, Ljung-Box, 80%/95% intervals, confidence label, `forecast_metric` tool with an interval band chart; no `split_by` |
| P13, P14 | ⬜ | |

## Details

| ID | Status | Note |
| --- | --- | --- |
| P0-01 | ✅ | `frontend/`, `backend/`, `docs/`, `data/` (gitignored) |
| P0-02 | ✅ | `/health`; env settings with `SLA_` prefix; `backend/.env.example` |
| P0-03 | ✅ | Routes: Chat, Data sources, Providers, Usage, Settings |
| P0-04 | ✅ | Alembic, applied on startup |
| P0-05 | ✅ | Covered by tests |
| P0-06 | ✅ | ruff + black, eslint + prettier, `tsc` (mypy skipped; optional) |
| P0-07 | 🟨 | pytest and Vitest; Playwright not set up |
| P0-08 | 🔍 | `.github/workflows/ci.yml` |
| P0-09 | 🔍 | `docker-compose.yml` with optional `ollama` profile (Docker unavailable here) |
| P0-10 | ✅ | JSON logs with `x-request-id` |
| P1-01…P1-11 | ✅ | Model list from `/models` with OpenRouter context, price and tool metadata; test connection and test tool calling; per-purpose defaults; encrypted, masked keys; delete guard; per-provider params; OpenRouter headers |
| P2-01…P2-14 | ✅ | Drag-and-drop with progress; size limit; delimiter, encoding, quote, header and skip override; 50-row preview; malformed-row count and samples; duplicate or missing headers; sanitized names; Parquet; full profile; pattern hints; key candidates; sampling; PII flags and masking |
| P3-01…P3-12 | ✅ | Sample count 0–20, strict mode, "what will be sent" preview, versioned prompt, JSON mode with fallback, repair loop (2 retries), confidence and rationale, DuckDB validation, cancellable background job, draft saved with provenance, whole-model and per-item regenerate |
| P4-01…P4-13 | ✅ | Editable column table, needs-attention sort and filter, metric editor with live test, ignore/PII, accept/override/regenerate, accept all, per-field provenance, diff view, save vs approve, raw JSON editor, approval blocked on errors, test-drive chat on the draft |
| P5-01…P5-09 | ✅ | Onboarding sets the active version; list with search and filter; detail page; draft-on-edit; rollback; refresh with schema diff; archive and delete; YAML/JSON export and import; resumable wizard |
| P6-01…P6-16 | ✅ | Spec, compiler, validation, sandboxed read-only executor (timeout, row cap), guarded `run_sql` (off by default), result object, the 6 tools, schema-generated registry, aggregates-only mode |
| P7-01…P7-13, P7-16 | ✅ | Streaming SSE, "How I got this", tables and charts, sessions, stop/retry/copy/export, agent loop with parallel tools, structured tool errors, clarification and grounding rules in the system prompt, pinned semantic version |
| P7-14 | 🟨 | Oldest turns are trimmed to a token budget; no summarization |
| P7-15 | ✅ | JSON-in-text tool protocol when `supports_tools = false` |
| P7 acceptance | ⬜ | Needs a run against one OpenRouter model and one Ollama model |
| P8-01, P8-02 | ⬜ | Golden-set eval harness and generation evals |
| P8-03, P8-04 | ✅ | Usage page with tokens, cost estimate, latency and errors; debug payloads |
| P8-05 | 🟨 | System prompt treats data as data; tools can't be widened; no adversarial test set |
| P8-06 | 🟨 | SDK retries with backoff (configurable); no client-side rate limiter |
| P8-07 | 🟨 | Pydantic limits on bodies and the upload size limit; not audited endpoint by endpoint |
| P8-08 | ✅ | Query cache keyed by (Parquet file + mtime, SQL, params) |
| P8-09 | ⬜ | Playwright E2E |
| P8-10 | 🟨 | Responsive layout, labels and keyboard-usable controls; no formal audit |
| P8-11 | 🟨 | README and `architecture.md` |
| P9-* | ⬜ | |
| P10-04, P10-06, P10-09, P10-11, P10-12 | ✅ | Inputs found from semantic roles and metrics; cache keyed on data, model and args; seed 42; segment- and period-level summaries only; prompt v2 requires quality measures and caveats |
| P11-01…P11-09, P11-11, P11-13, P11-15, P11-16 | ✅ | Tests include hand-computed RFM and planted clusters recovered with ARI > 0.9 |
| P12-01…P12-03, P12-05…P12-12, P12-14, P12-16…P12-18 | ✅ | Seasonal AR/MA terms only with two cycles left after differencing; fits with too few effective observations are rejected |
| P12 acceptance | 🟨 | Needs 3+ years of history; the sample has 24 months (`analysis/retail_analysis.py`) |

## Decisions log

| # | Question | Decision |
| --- | --- | --- |
| 1 | Raw SQL tool on by default? | Off (Settings toggle) |
| 2 | Default sample values per column | 5 |
| 3 | Chat retention | Kept until deleted |
| 4 | Single-user or auth? | Single-user, no auth |
| 5 | Default models | OpenRouter `openai/gpt-4o-mini`, Ollama `qwen2.5:7b-instruct` (edit `provider_defaults.yaml`) |
