# Semantic Layer AI

Ask questions about a CSV in plain English without sending the dataset to an LLM.

1. **Add an LLM provider:** OpenRouter (default), Ollama, or any OpenAI-compatible endpoint.
2. **Upload a CSV.** It is parsed, converted to Parquet and profiled locally with DuckDB.
3. **Generate a semantic layer.** The LLM sees only the *profile* (names, types, stats, a few masked samples) and proposes descriptions, roles, metrics and synonyms.
4. **Review it.** Accept, edit or regenerate each item, test metrics live, then approve.
5. **Chat.** The LLM sees only the approved semantic model and answers by calling local query tools. Every answer shows its tool calls and SQL.

## Quick start (local)

Requirements: Python 3.12+ and Node 18+.

```bash
./scripts/dev.sh            # creates backend/.venv, installs deps, runs both servers
PYTHON=python3.12 ./scripts/dev.sh   # if your default python3 is older than 3.12 (e.g. macOS's 3.9)
```

Then open http://localhost:5173, go to **Providers**, and add one:

- **OpenRouter:** paste an API key; everything else is pre-filled.
- **Ollama:** works with no changes if Ollama runs on `localhost:11434` with a tool-capable model (`ollama pull qwen2.5:7b-instruct`).

Or run the pieces yourself:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload            # API on :8000, runs DB migrations on start
cd frontend && npm install && npm run dev          # UI on :5173, proxies /api to :8000
```

Single-process mode: `cd frontend && npm run build`, then the backend serves the UI at http://127.0.0.1:8000.

## Analyses

In chat, the LLM can call two local analyses. Only segment- or period-level summaries reach the LLM.

- `segment_customers`: RFM scores and named segments, plus K-means on log-scaled RFM with k chosen by silhouette.
- `forecast_metric`: SARIMA with differencing from ADF/KPSS and STL seasonal strength, orders and drift by AIC, 80%/95% intervals, and a backtest against naive and seasonal-naive baselines.

`analysis/retail_analysis.py` runs the same code on the two-year Online Retail II workbook (`data/online_retail_II.xlsx`) and writes charts and a report to `analysis/output/`:

```bash
backend/.venv/bin/python analysis/retail_analysis.py
```

Docker: `docker compose up --build` (add `--profile ollama` for a local Ollama container). The UI is on :5173 and the API on :8000.

## Tests and checks

```bash
cd backend && .venv/bin/pytest -q && .venv/bin/ruff check app tests && .venv/bin/black --check app tests
cd frontend && npm run lint && npm test && npm run build
```

The backend suite (91 tests) covers ingestion edge cases, profiling, 37 compiler cases checked against hand-written SQL, PII and row-cap enforcement, the raw-SQL guard, the DuckDB sandbox, and a full API flow with a fake LLM (generation with a repair round, review, approval, streamed tool-calling chat, retry and export).

## Configuration

- Process settings: `backend/.env` (see `backend/.env.example`), such as the data directory, upload limit, encryption key and CORS.
- Runtime limits: the **Settings** page. It covers row caps, sample sizes, timeouts, agent iterations, privacy modes, debug payload logging, and default models for generation and chat.
- Provider defaults and suggested model IDs: `backend/app/provider_defaults.yaml`.

## Docs

- [Architecture, privacy model and semantic schema](docs/architecture.md)
- [Requirement-by-requirement progress](docs/PROGRESS.md)
