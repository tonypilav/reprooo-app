# Reprooo

**Turn incomplete task descriptions into structured requirements—with AI suggestions and human decisions.**

Reprooo is a hackathon MVP for PMs, analysts, QA engineers, and developers who need a shared understanding of a task before implementation and testing. It helps organize context, identify gaps, and refine a draft through explicit review.

[Try the public demo](https://reprooo-app.vercel.app/) using synthetic data only.

## The Problem

Incomplete task descriptions leave expected behavior, constraints, and acceptance criteria open to interpretation. Teams discover missing details during development or testing, leading to repeated clarifications, rework, and delays. Reprooo brings those questions into requirements review.

## How It Works

1. **Enter task context.** Paste a request, discussion, or other task description.
2. **Review masked data.** Choose optional masking categories, inspect the preview, and edit it if needed. The backend checks edited content again before model requests.
3. **Generate structured requirements.** Create a draft with Summary, Business Context, Requirements, Acceptance Criteria, and Open Questions.
4. **Run gap analysis separately.** An explicit action analyzes the current draft and returns up to five clarification questions, prioritized as blocking, recommended, or optional. Initial generation does not run this analysis automatically.
5. **Answer clarification questions.** Provide answers or mark questions as skipped, not applicable, delegated to implementation, or needing a stakeholder decision.
6. **Review proposed changes by section.** Inspect each proposed replacement and accept or reject it individually. AI suggestions do not change the canonical draft until accepted; manual editing is also available.
7. **Finalize and export.** Review the prepared snapshot, copy its summary or description, download Markdown, or submit to **Mock Jira**. After mock submission, review the result and reset to start again.

Summary, Business Context, and Acceptance Criteria must be nonempty for final preparation. Unresolved questions do not block finalization; you explicitly choose whether to include them in the final text. Field validation checks structure, while the user decides whether the requirements are ready.

## Key Features

- **Configurable masking:** optional categories plus mandatory protection for supported secret and token patterns. Original values are not restored into outputs.
- **Structured requirements template:** one backend-owned, five-section template (`requirements`, version `1`).
- **AI-powered gap analysis:** a separate review of missing or ambiguous information in the current draft.
- **Iterative refinement with explicit approval:** targeted section proposals, visible conflict flags, and individual accept/reject decisions. Rejecting a proposal protects its section until suggestions are explicitly re-enabled.
- **Session state and revision handling:** a canonical draft, question history, and revision checks help prevent stale updates. Manual edits remain local until an explicit operation synchronizes them.
- **Markdown export:** download the reviewed snapshot as `requirements.md`, or copy its summary and description.
- **Mock Jira submission:** create an in-memory demonstration ticket without contacting Jira.

## Architecture & Tech Stack

| Layer      | Implementation                                                                                                   |
| ---------- | ---------------------------------------------------------------------------------------------------------------- |
| Frontend   | React, Vite, Mantine; step-based interface and requirements editor                                               |
| Backend    | Python, FastAPI, Pydantic; separate orchestration, masking, refinement, validation, session, and adapter modules |
| LLM        | OpenAI Responses API with Structured Outputs, or a deterministic local stub                                      |
| State      | In-memory sessions and mock tickets; no persistent database                                                      |
| Jira       | Mock adapter only; no real Jira integration                                                                      |
| Deployment | Vercel (frontend) and Render (backend); runtime configuration has not been independently verified                |

See the [technical overview](docs/TECHNICAL_OVERVIEW_RU.md) for detailed architecture, API behavior, and Mermaid diagrams.

## Getting Started

### Prerequisites

- Python 3.11+ with `pip` and `venv` (required by the installed dependency metadata). The repository does not pin a Python runtime version.
- Node.js 22.13+ on the Node 22 release line, or Node.js 24+, with npm, to satisfy the locked frontend tooling requirements.
- An OpenAI API key with access to the configured model only if using OpenAI mode. Stub mode requires no key or model network calls.

The commands below use a POSIX shell and start from the repository root.

### Backend: local stub mode

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
APP_ENV=development CORS_ALLOWED_ORIGINS=http://localhost:5173 LLM_PROVIDER=stub \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 \
  --no-proxy-headers --no-access-log --log-level warning
```

Stub mode exercises the workflow offline: it copies the first nonempty context line into Summary and the full sanitized context into Requirements. It asks about empty required fields and proposes copying literal answers into empty sections. It does **not** perform semantic analysis; fill in missing Business Context and Acceptance Criteria yourself or through clarification answers.

### Frontend

In a second terminal, from the repository root:

```bash
cd frontend
npm ci
npm run dev -- --host localhost --port 5173 --strictPort
```

Open [the local frontend](http://localhost:5173). The development API URL defaults to `http://localhost:8000`; the backend allows the frontend origin `http://localhost:5173` by default. The [health endpoint](http://localhost:8000/api/health) checks the API handler, not OpenAI access or available usage budget.

### Environment configuration

No environment file is required for the stub commands above. These are the main settings:

| Variable               | Where        | Local value or behavior                                                                                    |
| ---------------------- | ------------ | ---------------------------------------------------------------------------------------------------------- |
| `LLM_PROVIDER`         | Backend      | `stub` by default; `openai` enables model-backed generation, analysis, and refinement                      |
| `OPENAI_API_KEY`       | Backend only | Required only in OpenAI mode; supply privately, never in frontend configuration                            |
| `OPENAI_MODEL`         | Backend      | `gpt-5.4-mini-2026-03-17`, the default and only allowed model profile                                      |
| `APP_ENV`              | Backend      | `development` by default                                                                                   |
| `CORS_ALLOWED_ORIGINS` | Backend      | `http://localhost:5173` when unset in development; explicit values replace the default                     |
| `VITE_API_BASE_URL`    | Frontend     | Optional in development; defaults to `http://localhost:8000`. Builds require an explicit HTTPS backend URL |

The backend supports local dotenv loading. The existing `backend/.env.example` and `frontend/.env.example` document configuration, but blank backend `APP_ENV` and `CORS_ALLOWED_ORIGINS` entries are not valid defaults: fill them in or omit them rather than copying unchanged. Never put API keys or other secrets in `VITE_*` variables; they are exposed in browser assets.

### Enable OpenAI mode

Stop the backend, securely provide `OPENAI_API_KEY` in its environment or a private `backend/.env` file, then restart from `backend` with the virtual environment active:

```bash
APP_ENV=development CORS_ALLOWED_ORIGINS=http://localhost:5173 \
  LLM_PROVIDER=openai OPENAI_MODEL=gpt-5.4-mini-2026-03-17 \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 \
  --no-proxy-headers --no-access-log --log-level warning
```

The key is deliberately omitted from the command. Startup validates configuration and key presence without verifying provider access. Model failures do not automatically fall back to the stub. Restart the backend after changing its configuration.

## Current MVP Limitations

- **Mock Jira only:** tickets exist in backend memory; returned Jira links are demonstration URLs.
- **No user authentication:** this is a demo, without user accounts or access controls.
- **No persistent storage:** sessions, refinement history, mock tickets, and usage counters are lost on backend restart. Reloading the browser does not automatically restore the draft.
- **Expiring sessions:** the sliding TTL is 900 seconds after successful session-changing operations. Passive editing and reading session state do not extend it.
- **Single-process architecture:** run one backend process and instance. Sessions, locks, and quotas are not shared across workers.
- **Rule-based masking:** original input reaches the Reprooo backend before masking. Detection does not guarantee removal of all confidential information; inspect previews and use synthetic data for public testing.
- **Bounded OpenAI usage:** limits include 12 paid dispatches per session, two concurrent model calls, request throttling, and a conservative $4 reservation budget per process lifetime. The budget resets on restart and is not a total spending guarantee. See the [abuse protection policy](docs/ABUSE_PROTECTION.md) for details.
- **Human review required:** structured output validation does not establish factual correctness or complete requirements. Review model-generated drafts, questions, and proposals.

## Documentation

Both detailed guides are in Russian:

- [Technical Overview](docs/TECHNICAL_OVERVIEW_RU.md) — architecture and Mermaid diagrams, modules, API workflow, security boundaries, and operational limitations.
- [Defense Cheatsheet](docs/DEFENSE_CHEATSHEET_RU.md) — concise technical Q&A for presenting the MVP and explaining its design decisions and limitations.
