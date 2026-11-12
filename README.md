# Reprooo

**AI-powered requirements refinement — from raw context to development-ready Jira tickets.**

Reprooo is a web application that transforms unstructured business requests, discussions, and technical context into clear, structured, and testable requirements.

Instead of simply rewriting task descriptions, Reprooo identifies missing information, ambiguities, contradictions, and potential edge cases before they become blockers during development or QA.

The goal is to improve task readiness, reduce clarification cycles, and enable QA engineers to participate earlier in the development lifecycle.

## The Problem

Software development teams frequently receive tasks with incomplete or ambiguous requirements.

Critical details — such as expected behavior, access permissions, error handling, dependencies, and acceptance criteria — are often discovered only after development has started or when a task reaches testing.

This results in additional discussions, rework, delayed testing, and inconsistent expectations between business stakeholders, developers, and QA engineers.

Reprooo addresses this problem by introducing an AI-assisted requirements refinement process before or during the early stages of development.

## How It Works

The target workflow consists of six stages:

1. **Provide context.** The user enters an initial business request, task description, or discussion copied from Slack. Supporting technical documentation may be attached where supported.
2. **Mask sensitive information.** The application detects and masks configured categories of sensitive data. The user reviews the masked content before AI processing. Credentials and secrets must never be restored into generated tickets.
3. **Generate an initial draft.** An LLM analyzes the context and produces a structured Jira task, highlighting missing requirements, ambiguities, contradictions, and potential edge cases.
4. **Refine requirements iteratively.** The user answers clarification questions, edits the draft, and requests another analysis. Each iteration updates only relevant sections while preserving previously approved requirements.
5. **Finalize the task.** The user reviews the resulting requirements, acceptance criteria, and unresolved questions before deciding whether the task is ready.
6. **Create a Jira ticket.** The finalized task is submitted through a Jira adapter. The hackathon MVP uses a mock implementation; real Jira Cloud integration is planned for future development.

The user controls the number of refinement iterations and makes the final decision about task readiness.

## Key Capabilities (Target Product)

- **Context-aware analysis:** identifies potential gaps based on the type of task and the supplied information.
- **Structured requirements:** generates business context, functional requirements, acceptance criteria, constraints, and relevant edge cases.
- **Clarification questions:** distinguishes blocking questions from recommended and optional clarifications.
- **Incremental refinement:** updates existing drafts without unnecessarily rewriting previously approved requirements.
- **Sensitive-data masking:** configurable masking rules with a user-facing preview.
- **Template-driven generation:** task structure is defined through configuration rather than hardcoded into the UI.
- **Human-in-the-loop workflow:** the AI suggests improvements but does not invent or independently approve business decisions.
- **Jira-ready output:** produces a structured task suitable for review and submission.

## Technology Stack

| Layer                   | Technology                                   |
| ----------------------- | -------------------------------------------- |
| Frontend                | React, JavaScript, Mantine, Vite             |
| Backend                 | Python, FastAPI, Pydantic                    |
| LLM integration         | OpenAI API                                   |
| Orchestration           | Modular Python orchestration layer           |
| Sensitive-data handling | Configurable masking rules                   |
| Session management      | In-memory session store with TTL             |
| Task templates          | Configuration-driven schemas                 |
| Jira integration        | Adapter pattern, mock implementation for MVP |

## Architecture

The application uses a modular architecture separating the user interface, orchestration, sensitive-data processing, LLM integration, requirements validation, session management, and Jira integration.

The intended processing flow is:

`User Context → Masking → LLM Draft Generation → Completeness Analysis → Iterative Refinement → Final Review → Jira Adapter`

Draft generation and completeness analysis are logically separate responsibilities.

Refinement should be incremental: previously approved requirements must remain unchanged unless the user explicitly authorizes modifications.

Sensitive data must be handled before external LLM requests. Supporting documentation is subject to the same security considerations as user-provided context.

## Hackathon MVP

The MVP focuses on demonstrating the complete requirements refinement workflow:

- Enter unstructured context.
- Configure and review sensitive-data masking.
- Generate an initial structured task using an LLM.
- Identify missing requirements and generate clarification questions.
- Refine the draft through multiple iterations.
- Preserve previously approved requirements.
- Review and finalize the resulting task.
- Simulate Jira ticket creation.
- Reset the workflow after successful submission.

### Current Implementation Status

The prototype implements context input, configurable masking and preview review,
a general-purpose requirements template, field editing, safe in-memory sessions,
and mock Jira submission. Its one active template is `requirements`, version `1`.

By default, generation is deterministic and offline: the first nonempty sanitized line becomes
`summary`, the entire sanitized input becomes `requirements`, and the other fields
remain empty. Users must supply missing business context and acceptance criteria.
Task 5 adds iterative gap analysis, partial answers and explicit dispositions,
section proposals, independent accept/reject decisions, and protected sections.
The session owns the sanitized draft and revision; edits stay local until an action.
The offline stub asks only about empty required fields and copies literal answers
into empty sections. It does not perform semantic analysis. Configured OpenAI mode
supports initial generation, gap analysis and targeted refinement through the same
Responses API client and strict Structured Outputs. Jira remains mock-only.


### Clarification API (requirements v1)

- `POST /api/clarifications/analyze`: synchronize reviewed edits and return 0–5 questions.
- `POST /api/clarifications/refine`: submit partial `answers` with `question_id`,
  `status`, and literal `answer`; return proposals without applying them.
- `POST /api/clarifications/decisions`: submit `batch_id` and individual
  `{proposal_id, action: "accept"|"reject"}` decisions, or `allow_sections` to
  re-enable suggestions. Rejections protect sections for the session.
- `GET /api/sessions/{session_id}/refinement`: recover the current checkpoint;
  does not refresh the 900-second session TTL.

Mutations carry `session_id`, `template: {mode: "requirements", version: 1}`,
`expected_revision`, a fresh client `request_id`, and the current five-field `draft`.
Identical retries reuse their request ID; the last 32 successful IDs retain keyed
request digests, never raw inputs. Reusing an ID with changed content or submitting
an obsolete revision returns safe `409 REVISION_CONFLICT`. Recovery is explicit;
local edits are preserved until the user chooses to replace them. All explicit
question-status corrections are allowed; only `answered` carries answer text.
Resolved normalized duplicates are suppressed unless changed associated sections
provide new quoted evidence. Earlier dispositions remain in the ledger.
The compact ledger retains decisions and answers for this ephemeral session only.

Sanitization changes return `preview_review_required` with reviewed `draft` and
`answers`; resubmit the sanitized content after reviewing it. A signed, sanitized
`review_token` carries any history/proposal redactions into that retry without
storing original values or a pending draft. Return the token with the reviewed
mutation; its session and revision are verified, and the whole payload is rechecked. These responses do
not commit a draft or refresh TTL. Successful mutations commit atomically and
refresh TTL; failed operations do neither. A cancelled HTTP request can leave an
admitted worker finishing successfully; recover via GET or retry the same request.

Generation additionally returns `revision`. Task 5 submission adds
`expected_revision` and `request_id` to `/api/create-ticket`; older clients can omit
both. Submission consumes the session once, including after client cancellation;
subsequent requests return `SESSION_UNAVAILABLE`, never another ticket. Before Jira,
the UI previews unresolved/delegated additions to `open_questions`. Incorporating
or declining them is explicit; neither choice blocks submission. No database
migration is required; restart/regenerate old in-memory sessions.

## Generation configuration and offline verification

Backend environment configuration (restart the backend after changing it):

| Variable | Behavior |
| --- | --- |
| `LLM_PROVIDER` | `stub` (default) or `openai`; unknown values fail application initialization. |
| `OPENAI_API_KEY` | Required and nonempty only for `openai`; keep exclusively in the backend environment. |
| `OPENAI_MODEL` | Configurable model, default `gpt-5.4-mini`; verify access in your OpenAI project before a manual demo. |

Use `LLM_PROVIDER=stub` for offline development. For real generation, configure
`LLM_PROVIDER=openai` and provide `OPENAI_API_KEY` securely in the backend environment
or its local dotenv file. Never put credentials in frontend/Vite configuration.
Application initialization validates the provider and key presence without a network
request. It does not verify authentication or model access.

OpenAI requests use a 60-second SDK timeout, zero automatic SDK retries, and
`store=False`. There is no automatic fallback to the stub. Only sanitized context, current draft, question/answer state and static instructions
cross the model boundary. User edits
that introduce sensitive content require a revised preview confirmation first.

The model must return exactly the five requirements-v1 string fields. Unsupported
information remains empty; supplied expected behavior may be rewritten as acceptance
criteria without adding facts. `open_questions` stays empty in Task 4. The backend
validates and sanitizes output, owns template metadata, and computes `missing_fields`.
Structured output and instructions do not guarantee factual accuracy: review the draft.

Generation failures return safe `502 / LLM_GENERATION_FAILED`; timeouts return
`504 / LLM_TIMEOUT`. Provider details are not displayed. The UI retains input and
preview for retry. Failures neither consume the session nor refresh its 900-second
TTL; an expired session uses existing restart behavior. Jira submission remains mock-only.

Run offline verification from the repository root (backend tests mock OpenAI):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend backend/.venv/bin/python -B -m unittest discover -s backend/tests -q
```

From `frontend`:

```bash
node --test src/api.test.js
npm run lint
npm run build
```

## Expected Impact

Reprooo aims to improve the quality of requirements before development and testing.

Potential effectiveness metrics include:

- Reduction in clarification questions raised during QA.
- Reduction in tasks returned for requirements clarification.
- Time required to prepare development-ready tasks.
- Number and severity of missing requirements identified before development.
- Number of refinement iterations per task.
- Frequency of changes to acceptance criteria after development begins.

These are proposed evaluation metrics, not measured outcomes of the current prototype.

## Future Development

- **Knowledge base:** reusable specifications, documentation, and team-specific requirements.
- **Team and service context:** automatically retrieve relevant documentation based on the selected team or service.
- **Repository integration:** use relevant technical specifications and code context when analyzing requirements.
- **Specification generation:** preserve technical specifications alongside pull requests.
- **Real Jira Cloud integration:** create and update tasks directly.
- **Slack integration:** import discussions without manual copying.
- **Organizational learning:** reuse previously identified requirement gaps, clarification patterns, and team conventions.
- **Company-approved LLM providers:** support deployment using approved internal or external models.
- **Analytics:** measure requirements quality and refinement effectiveness.

## Project Documentation

For detailed product requirements, architectural decisions, MVP boundaries, and acceptance criteria, see:

- [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) — product vision, target workflow, and requirements.
- [`AGENTS.md`](AGENTS.md) — repository instructions for AI coding agents.

These documents should be consulted before implementing significant changes to the application.

## Current masking security boundaries

Credentials matching supported password/API-key assignments, quoted assignments,
JWT/provider-token patterns, and Basic/Bearer Authorization formats are redacted
before generation and mock ticket storage. Detected values repeated within one
processing operation are also redacted. Arbitrary unlabeled values and unknown
credential formats are outside this detection guarantee.

Session masking state stores rule choices and placeholder/category metadata, not
original values. No category is restored at this stage. Selective restoration
remains planned for final review and will require a category-specific policy and
explicit confirmation; personal data stays masked by default. Optional masking
chips remain configurable, while credential rules cannot be disabled.

Editing a preview triggers another backend check. If it changes the text, the
updated preview must be reviewed and submitted again before generation. The
default generator is an offline stub; explicitly configured OpenAI mode is available.
Session validity and the expiry lifecycle are implemented as described below.

Synthetic-only backend verification (using existing environment dependencies):

```sh
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
```

## In-memory session lifecycle

Sessions expire after 900 seconds without a successful session-backed operation.
Generation and sanitized-preview review refresh the deadline from completion;
failures, lookups, cleanup, and passive browser editing do not. Successful mock
submission consumes the session; explicit deletion removes it. A session is
expired at its deadline, not one instant later.

The three session-dependent API handlers admit requests asynchronously after
request validation and before scheduling synchronous worker execution. This is
API admission time, not exact HTTP socket arrival time. Eligibility is frozen
against the deadline at admission: requests arriving at this step after expiry
are rejected even while earlier work is running. An admitted request can wait
for worker capacity and its session lock, then finish after the original deadline.
It still must refer to the same session, not a deleted or consumed entry.

A reservation pins each admitted operation against cleanup. Pending reservations
belong to the API; cancellation releases unused admission without refreshing TTL.
Once a worker claims a reservation, it owns release until processing finishes,
including while it waits for the session lock. Cancelling the HTTP await cannot
release that worker's pin or interrupt it midway through a session update.
Already running work may still succeed (and refresh or consume the session) after
the client stops waiting. Release is exactly once. Processing uses private masking
metadata; failed work publishes no partial updates. Requests for one session are
serialized, with no global lock held during generation or Jira processing.

FastAPI lifespan performs an initial sweep and starts one cleanup task running
every 30 seconds. It skips admitted work without waiting on operation locks. The
task is cancelled and awaited on shutdown. Storage is single-process and lost
on restart; use one application worker for the demo.

Unknown, expired, deleted, and consumed session IDs return HTTP 404 with
`detail.code = SESSION_UNAVAILABLE`. Missing or malformed IDs return safe HTTP
422 `INVALID_REQUEST` errors. The UI disables further session actions while
keeping the visible preview/draft. Explicit restart preserves the original
context and masking selections, but discards the old preview/draft. No browser
storage, heartbeat, automatic draft migration, or live integration is added.

Frontend checks (from `frontend`):

```sh
node --test src/api.test.js
npm run lint
npm run build
```

## Requirements template v1 and API migration

The backend-owned registry publishes exactly one active template through
`GET /api/templates/requirements`. The response includes `mode`, `version`, and
an ordered list of fields, labels, hints, and required flags:

| Field ID | UI label | Required for submission |
| --- | --- | --- |
| `summary` | Краткое описание | Yes |
| `business_context` | Бизнес-контекст | Yes |
| `requirements` | Требования | No |
| `acceptance_criteria` | Критерии приёмки | Yes |
| `open_questions` | Открытые вопросы | No |

All five fields are plain text. Acceptance criteria and open questions may use
newline-separated bullets; there are no structured question objects or approval
states. Requirements may include known constraints and error cases. Unconfirmed
business details must remain unknown rather than be filled with `TBD` or invented
content. `missing_fields` checks required-field emptiness, not semantic readiness.

Generation keeps the `draft` and `missing_fields` properties and adds the exact
reference used for that draft:

```json
{
  "template": {"mode": "requirements", "version": 1},
  "draft": {
    "summary": "Add a test-message button",
    "business_context": "",
    "requirements": "Add a test-message button\nPlace it in service settings.",
    "acceptance_criteria": "",
    "open_questions": ""
  },
  "missing_fields": ["business_context", "acceptance_criteria"]
}
```

Submission requires that reference alongside the edited draft:

```json
{
  "session_id": "AAAAAAAAAAAAAAAAAAAAAA",
  "template": {"mode": "requirements", "version": 1},
  "draft": {
    "summary": "Add a test-message button",
    "business_context": "Allow operators to verify message delivery.",
    "requirements": "Add the button in service settings.",
    "acceptance_criteria": "Clicking the button sends one test message.",
    "open_questions": ""
  }
}
```

The backend rejects unknown field IDs, non-string values, and unsupported template
references without echoing submitted content. Missing allowed fields normalize to
empty strings in template order. Empty required fields are allowed during generation,
but empty or whitespace-only required submission fields fail with HTTP 422.
Structural/reference errors use safe `INVALID_REQUEST` responses; incomplete drafts
retain the existing `missing_fields` error. Invalid submissions do not consume the
session or refresh its TTL. Template compatibility is checked inside the admitted
operation. The existing preview-review and session-unavailable contracts are unchanged.

This is a coordinated frontend/backend API migration: the old `bug` template and
`title` field are not aliases. Older clients/drafts must be reloaded and regenerated;
there is no automatic field conversion or historical-template support. `summary`
maps to mock Jira Summary; nonempty remaining sections appear in configured order.
A template's version identifies its structure, not a draft revision.

The editor verifies the loaded template against the generated reference. If loading
fails or metadata differs, the generated draft remains in local React state for
an explicit retry. No replacement session or draft is created automatically.

## Production connectivity (Task 7.2)

Local development needs no new configuration: the frontend uses
`http://localhost:8000` and the backend allows `http://localhost:5173`.
Example files list variable names only; fill their blank values before use rather
than copying them unchanged.

On **Vercel**, set `VITE_API_BASE_URL=https://api.example.com` before building.
It must be an explicit HTTPS backend URL; missing or invalid values fail the build.
Trailing slashes are removed; an optional base path is supported. URLs containing
credentials, query strings, or fragments are rejected. Rebuild after changing it.
Custom Vite build modes require this setting too.

On **Render**, set `APP_ENV=production` and
`CORS_ALLOWED_ORIGINS=https://demo.example.com`.
Multiple exact origins can be comma-separated. Production requires HTTPS origins;
missing, blank, malformed, wildcard, or localhost production values fail application
initialization. Origins cannot contain paths or trailing slashes. Explicit configuration
replaces the local default; include local origins only in development configuration
when needed. No Vercel preview domains are allowed automatically. Credentials remain
disabled, with existing allowed methods and headers unchanged.

`APP_ENV` defaults to `development` and only accepts `development` or `production`.
`CORS_ALLOWED_ORIGINS` defaults to the local Vite origin only when unset in development.
Set production mode explicitly on Render; it is not inferred from the hosting service.

OpenAI API keys belong only in backend environment settings. **Never put keys or other
secrets in `VITE_*` variables**: Vite embeds these values in public browser assets.
The public API URL is not a secret. CORS controls browser access, not authentication
or abuse prevention. Public API protection and resource limits remain required before
deployment. This task adds no deployment files or live integrations.

Connectivity verification (synthetic data, no live OpenAI calls):

```sh
cd frontend
node --test src/*.test.js
npm run lint
VITE_API_BASE_URL=https://api.example.com npm run build
# Without VITE_API_BASE_URL, a build is expected to fail.
```

Run the backend suite from the repository root using the command documented above.

## Demo request and memory limits

Task 7.3.1 adds a 256 KiB pre-parser HTTP body limit, bounded schemas and refinement
history, and atomic 100-session / 100-mock-ticket capacity limits. See
[request and memory limits](docs/REQUEST_MEMORY_LIMITS.md) for exact budgets,
configuration, rejection contracts, receipt sizing, frontend behavior and risks.
