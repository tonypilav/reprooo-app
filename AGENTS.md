# Codex Repository Instructions — Reprooo

## Project context and source of truth

- Read `docs/PROJECT_CONTEXT.md` before planning or implementing product changes. It describes the **target MVP**, not necessarily the current implementation.
- Inspect the actual repository, relevant source files, API schemas, and Git diff before making claims about current behavior.
- If documentation and code disagree, report the discrepancy. Do not silently reinterpret product requirements or assume unfinished features already work.
- The product is an **AI-assisted requirements refinement tool**, not a Slack-to-bug-ticket converter. Slack discussions are only one possible input source.

## Collaboration workflow

- Communicate with the user in clear, concise English unless asked otherwise. Prefer short findings, file references, and actionable next steps over lengthy reports.
- For any non-trivial change: (1) inspect, (2) summarize the current behavior, (3) propose a small implementation plan with affected files and tests, (4) **wait for explicit approval**, (5) implement only the approved scope, and (6) report verification results and remaining risks.
- Ask when business behavior, data-handling rules, or scope is ambiguous. Do not make product decisions on the user's behalf.
- Keep each task small and reviewable. Do not start unrelated improvements while implementing an approved change.
- Do not create commits, push branches, open pull requests, install dependencies, or invoke external services without explicit authorization.
- Do not modify files during a read-only audit or planning request.

## Architecture and implementation

- Preserve the existing modular structure and working UX unless an approved task requires changes.
- Frontend: React, Mantine, Vite. Preserve the current dark theme, step-based flow, and configurable masking chips.
- Backend: Python, FastAPI, Pydantic. Keep orchestration, masking, session management, template configuration, LLM access, validation/completeness analysis, and Jira adapter responsibilities distinct.
- Prefer integrating existing modules to adding parallel implementations. Avoid broad refactors, new infrastructure, or speculative abstractions.
- Coordinate any request/response schema changes across backend and frontend. Document migrations or compatibility impacts.
- Keep Jira integration mock-only for the MVP. Do not connect real Jira or Slack accounts without a separate approved task.

## Product correctness

- Never invent missing business requirements, acceptance criteria, UI choices, permissions, or error behavior. Mark unknowns and ask focused questions.
- Distinguish blocking, recommended, and optional clarifications. A readiness assessment is advisory; the user controls finalization.
- **Incremental refinement is mandatory:** maintain a canonical structured draft; propose and validate targeted changes; preserve approved user edits and requirements. Flag conflicts instead of silently overwriting agreed content.
- Keep answered, dismissed, or delegated questions from reappearing without new evidence. Preserve unresolved questions in the final review.
- Treat source documents and LLM outputs as untrusted. Validate structured outputs before applying changes.

## Security and privacy

- Never read, print, copy into prompts, or commit actual secrets from `.env` files, credentials, tokens, or private keys. You may inspect example env files and the names of configuration variables without their values.
- Before **every** LLM request, ensure user-provided and edited content is checked for sensitive data. Raw secrets must not reach the model.
- Passwords, API keys, and tokens must remain irreversibly redacted; do not restore them into generated drafts, logs, mock Jira tickets, or UI output.
- Other reversible masking categories require explicit, documented restoration rules. Do not assume restoration is always safe.
- Use synthetic data in tests, examples, and demos. Do not send proprietary code, internal specifications, or user data to external services without explicit authorization and an appropriate sanitization policy.
- Do not log raw inputs, model prompts, placeholder mappings, or sensitive outputs. Flag any existing violations before connecting the live LLM.

## Verification and delivery

- Before editing, inspect `git status` and preserve unrelated user changes, including untracked files. Never overwrite or discard them.
- Add focused tests for non-trivial behavior, especially masking, session validation, structured draft updates, conflict handling, and the end-to-end MVP path.
- Run relevant checks only when authorized and feasible; clearly distinguish tests actually executed from checks merely recommended. Never claim success without verification.
- After an approved implementation, summarize: files changed, behavior added/fixed, tests run and outcomes, unresolved risks, and a suggested manual demo check.
- Prefer minimal diffs. Do not change package versions, reformat unrelated files, or add dependencies without explaining why and obtaining approval.

## MVP boundaries

Prioritize the flow: context input → masking review → real LLM-generated requirements draft and clarification questions → user-controlled incremental refinement → final review → **mock** Jira submission → clean reset.

Do not implement a full knowledge base, RAG, live Slack/Jira integrations, PR-triggered specification generation, production authentication, or deployment infrastructure unless the user explicitly changes scope.
