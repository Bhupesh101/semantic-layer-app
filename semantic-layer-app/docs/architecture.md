# Architecture

```
[React UI] ──REST / SSE──> [FastAPI]
                              ├── providers      OpenAI-compatible client per provider (OpenRouter / Ollama / custom)
                              ├── ingestion      sniff → preview → CSV→Parquet (DuckDB)
                              ├── profiler       SUMMARIZE + per-column stats, pattern hints, PII flags, key candidates
                              ├── semantic       generate (LLM, repair loop) · validate · versions (draft/approved/superseded)
                              ├── query          spec → SQL compiler · sandboxed DuckDB executor (timeout, row cap, cache)
                              ├── chat           tool registry · agent loop (streaming, parallel tools, text fallback)
                              └── SQLite         providers, sources, profiles, semantic versions, jobs, chats, LLM call log
```

| Path | What lives there |
| --- | --- |
| `backend/app/services/ingestion.py` | Encoding/delimiter detection, preview, malformed-row report, Parquet conversion |
| `backend/app/services/profiler.py` | Column profile used as the only LLM input during generation |
| `backend/app/semantic/` | Schema (Pydantic), generator + prompt, validation, versioning and provenance |
| `backend/app/query/` | Query spec, compiler, expression checks (sqlglot), sandboxed executor |
| `backend/app/chat/` | Tools exposed to the LLM, orchestrator (agent loop), SSE events |
| `backend/app/prompts/` | Versioned prompt templates (`semantic_generation_v1`, `chat_system_v1`) |
| `backend/migrations/` | Alembic migrations, applied automatically on startup |
| `frontend/src/pages/` | Providers, Data sources, onboarding wizard, Review, Source detail, Chat, Usage, Settings |

## Privacy model

| Stage | What the LLM receives | What it never receives |
| --- | --- | --- |
| Generation | Column names, types, null %, distinct counts, numeric/date ranges, top values and up to N sample values per column (N = 0–20, default 5). PII columns: masked samples only, no min/max. Strict mode: names, types and stats only. | Rows, files, unmasked PII |
| Chat | The approved semantic model (ignored columns removed) and tool results: aggregates, distinct values, at most 50 non-PII rows from `get_rows`, all capped at the row cap (default 200) | The file, ignored columns, raw PII values |

- The **Preview what will be sent** button shows the exact generation messages. With debug mode on, every LLM payload is stored in the usage log.
- Queries run in an in-memory DuckDB connection that can read only that source's Parquet file. External access, `ATTACH`, `COPY`, extension installs and config changes are blocked, with a timeout (default 10 s).
- The LLM never writes SQL directly by default. It fills a JSON query spec that the compiler validates against the model: only known fields, no ignored columns, PII columns can only be counted, and values are bound as parameters.
- The optional `run_sql` tool (off by default) accepts a single SELECT on `src`, with no `*` and PII only inside `COUNT()`.
- API keys are Fernet-encrypted at rest and only ever returned masked (`sk-…abcd`).

## Semantic model schema

Defined in `backend/app/semantic/schema.py`, following spec §3.1. One deviation: allowed values live on each column (`allowed_values`) instead of a top-level map.

```yaml
entity: {name, display_name, description, grain}
columns:
  - name            # sanitized column name (must exist in the data)
    display_name, description
    data_type       # string|integer|float|boolean|date|datetime
    role            # dimension|measure|time_dimension|identifier|ignore
    default_aggregation  # measures: sum|avg|min|max|count|count_distinct
    unit, format, synonyms[], allowed_values[], pii
    confidence, rationale        # from the AI draft
    provenance: {field: ai_regenerated|user_override|user}   # absent = ai
    review_status: pending|accepted
metrics:
  - name, display_name, description
    expression      # DuckDB aggregate SQL over column names, e.g. SUM(quantity * price)
    filters[]       # SQL conditions applied as FILTER (WHERE …) to every aggregate
    unit, synonyms[]
sample_questions[]
relationships[]     # reserved for multi-table sources (P9)
```

Each version keeps a `base_json`, the AI draft or the version it was copied from. Provenance and the review diff are computed against that base.

## Query spec (what `query_metrics` accepts)

```json
{"metrics": ["revenue", "avg(price)", "row_count"], "dimensions": ["country"],
 "filters": [{"field": "status", "op": "in", "value": ["completed"]}],
 "time_dimension": "invoicedate", "time_grain": "month",
 "time_range": {"start": "2011-01-01", "end": "2012-01-01"},
 "order_by": [{"field": "revenue", "direction": "desc"}], "limit": 10}
```

Names resolve case-insensitively through names, display names and synonyms. Filters on metrics become `HAVING`.

## Adding a provider type

Every provider goes through the OpenAI-compatible SDK with a configurable `base_url`, so most new providers need no code.

1. Add an entry to `backend/app/provider_defaults.yaml` (label, base URL, whether a key is required, default and suggested models).
2. Add the type to `PROVIDER_TYPES` in `services/providers.py` and to the `ProviderType` literal in `routers/providers.py` and `frontend/src/lib/types.ts`.
3. If the provider needs special headers or richer `/models` metadata, extend `_headers()` and `list_models()`.

Models without native tool calling (`supports_tools = false` after **Test tool calling**) automatically use a JSON-in-text tool protocol.
