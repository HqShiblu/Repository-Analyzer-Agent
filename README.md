# Codebase Research Agent

A Django REST Framework AI agent that accepts a public GitHub repository URL and a
natural-language question, intelligently traverses the codebase using the GitHub
API via **tool calling**, and returns a precise answer with **references to
specific files, functions, and line numbers**. Every research session is
persisted to PostgreSQL so it can be retrieved, reviewed, and built upon later.

The pipeline cost-ladders questions through four progressively more expensive
sources before doing real work:

```
cache  →  llm_knowledge  →  readme_scan  →  full_traversal
```

See [`DECISIONS.md`](./DECISIONS.md) for design decisions (including the four-model
layout) and [`SPECS.md`](./SPECS.md) for the full specification.

---

## Data model (short)

| Model | Role |
|-------|------|
| `Repository` | One row per GitHub repo URL (`url` unique). |
| `ResearchSession` | One row per API question; FK to `Repository`; question embedding, answer, source, token usage. |
| `Finding` | Many rows possible per session; agent notes from `save_finding`; reused via `get_previous_findings()` on later sessions. |
| `ToolCallLog` | Many rows per session; written automatically after each tool call (`session_id` FK); **not** an LLM tool. |

Detail and field lists: **[Database Schema in SPECS.md](./SPECS.md#database-schema)**.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django 5 + Django REST Framework |
| Database | PostgreSQL with the `pgvector` extension |
| Vector search | pgvector cosine similarity on question embeddings |
| LLM | Any OpenAI-compatible API that supports tool calling |
| Embeddings | `sentence-transformers` (`all-MiniLM-L6-v2`, 384-d, local) |
| GitHub data | GitHub REST API (token-authenticated) |

---

## Project Layout

```
.
├── manage.py
├── requirements.txt
├── .env.example
├── README.md
├── DECISIONS.md
├── SPECS.md
├── conftest.py
├── fixtures/
│   └── sample_sessions.json
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
└── agent/
    ├── models.py
    ├── serializers.py
    ├── views.py
    ├── urls.py
    ├── migrations/
    ├── management/commands/
    │   └── seed_sample.py
    ├── tests/
    │   ├── test_sanitizer.py
    │   ├── test_classifier.py
    │   ├── test_references.py
    │   ├── test_outline.py
    │   ├── test_tools.py
    │   ├── test_pipeline.py
    │   └── test_views.py
    └── services/
        ├── sanitizer.py        # URL normalization
        ├── embeddings.py       # local sentence-transformers
        ├── cache.py            # semantic cache via pgvector
        ├── github.py           # GitHub REST client (raw + line-numbered content)
        ├── classifier.py       # question theme & path ranking
        ├── outline.py          # language-aware outlines + method bodies
        ├── llm.py              # OpenAI-compatible chat wrapper
        ├── tools.py            # agent tools + automatic ToolCallLog
        ├── agent_loop.py       # tool-calling loop + token logging
        └── pipeline.py         # top-level orchestrator
```

---

## Setup

### 1. Clone & create a virtual environment

```bash
git clone <this-repo-url>
cd "CodeFusion Chat API"
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Set up PostgreSQL with pgvector

```bash
# Once per Postgres instance:
psql -U postgres -c "CREATE DATABASE github_analyzer;"
psql -U postgres -d github_analyzer -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 3. Configure `.env`

```bash
cp .env.example .env
# Edit .env: set LLM_API_KEY, GITHUB_TOKEN, DB_* values.
```

Key environment variables:

| Variable | Purpose |
|---|---|
| `LLM_BASE_URL` | OpenAI-compatible endpoint (`https://api.openai.com/v1`, Groq, local Ollama, …) |
| `LLM_MODEL_NAME` | Model that supports tool/function calling (e.g. `gpt-4o`) |
| `LLM_API_KEY` | API key for the LLM provider |
| `EMBEDDING_MODEL_NAME` | `all-MiniLM-L6-v2` (384-d) by default |
| `GITHUB_TOKEN` | Personal access token — required for >60 req/hr |
| `AGENT_MAX_LOOP` | Max tool-invocation rounds per traversal (default **30** if unset) |
| `AGENT_MAX_FILE_READS` | Max **distinct files** touched by `read_file` / first `read_method` per session (default **15**) |
| `SEMANTIC_CACHE_THRESHOLD` | Cosine similarity floor for cache hits (default **0.92**) |
| `DB_*` | PostgreSQL connection details |

For the full `.env` list and behavior, see [`SPECS.md` § Environment Variables](./SPECS.md#environment-variables-env).

### 4. Migrate & run

```bash
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

---

## API

### `POST /api/sessions/` — start a new research session

```bash
curl -X POST http://localhost:8000/api/sessions/ \
  -H "Content-Type: application/json" \
  -d '{
    "repository_url": "https://github.com/tiangolo/fastapi",
    "question": "How does FastAPI handle dependency injection internally?"
  }'
```

Response (truncated):

```json
{
  "session_id": "…",
  "repository_url": "https://github.com/tiangolo/fastapi",
  "question": "How does FastAPI handle dependency injection internally?",
  "answer": "FastAPI resolves dependencies via … [[fastapi/dependencies/utils.py:42-78]] …",
  "source": "full_traversal",
  "references": [
    {
      "file_path": "fastapi/dependencies/utils.py",
      "line_start": 42,
      "line_end": 78,
      "note": "Core dependency resolver entry point."
    }
  ],
  "token_usage": {"prompt_tokens": 3200, "completion_tokens": 410, "total_tokens": 3610},
  "created_at": "…",
  "completed_at": "…"
}
```

### `GET /api/sessions/<session_id>/` — full session detail

The path parameter is the **`ResearchSession` primary key** (UUID in the current schema).
The response includes every `Finding` and every `ToolCallLog` for that session.

```bash
curl http://localhost:8000/api/sessions/<session-id>/
```

If the session is not found, the API returns a minimal error object (e.g. `404` / `Session not found`). Upstream LLM or GitHub failures on `POST` use the same minimal shape (e.g. `502` / `504`) without raw provider errors in the body.

### `GET /api/sessions/?repo=<url>` — past sessions for a repo

```bash
curl "http://localhost:8000/api/sessions/?repo=https://github.com/tiangolo/fastapi"
```

### `GET /api/repos/` — all researched repos

```bash
curl http://localhost:8000/api/repos/
```

---

## What the agent prints

Every tool call is logged to stdout in real time. Each line shows **`tokens this
round`** for that LLM completion only (not a running sum). File-touching tools include
the path in parentheses. After the loop, the **session** cumulative total is printed:

```
[Tool Call 1/30] get_directory_tree |  tokens this round: 412
[Tool Call 2/30] get_previous_findings |  tokens this round: 698
[Tool Call 3/30] read_file (README.md) |  tokens this round: 842
[Tool Call 4/30] save_finding (README.md) |  tokens this round: 842
[Tool Call 5/30] read_file (src/main.py) |  tokens this round: 1,100

Total tokens used: 3,610
```

The cumulative total is persisted to `ResearchSession.token_usage`.

---

## Testing

Tests run on SQLite without any external services. Embeddings, the LLM, and
the GitHub API are mocked.

```bash
# pytest is convenient; pytest-django picks up conftest.py automatically.
pip install pytest pytest-django
pytest -q
```

Or via Django's test runner:

```bash
USE_SQLITE=1 python manage.py test agent
```

Key tests:
- `test_sanitizer.py` — URL normalization edge cases
- `test_classifier.py` — question theme detection + path ranking
- `test_references.py` — `[[path:line_start-line_end]]` parsing
- `test_outline.py` — outline regex + method body extraction
- `test_tools.py` — dispatcher + automatic `ToolCallLog` writes
- `test_pipeline.py` — cache, `llm_knowledge`, and `full_traversal` branches
- `test_views.py` — sanitized API error responses

---

## Operational notes

- **Synchronous request/response.** Long-running sessions on huge repos will
  hit the HTTP timeout. Moving the agent loop to Celery would be the natural
  next step.
- **GitHub rate limits.** Unauthenticated callers get 60 req/hr; set
  `GITHUB_TOKEN` for 5000 req/hr.
- **Cost / breadth limits.** `AGENT_MAX_LOOP` caps tool rounds; `AGENT_MAX_FILE_READS`
  caps how many **different files** get full/raw body reads in one session. Tune both in `.env`.
- **Sanitized errors.** Failed `POST /api/sessions/` responses omit upstream provider
  payloads; see SPECS / `test_views.py` for intended behavior.
- **No clones.** Files are only ever fetched via the GitHub REST API.

See [`DECISIONS.md`](./DECISIONS.md) for the trade-offs, known limitations,
and what we'd do differently with more time.
