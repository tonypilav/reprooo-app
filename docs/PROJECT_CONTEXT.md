# Reprooo — Product Context and MVP Requirements

**Status:** Product requirements for the next development phase  
**Audience:** Codex and project contributors  
**Last updated:** 2026-11-11

> This document defines the intended product and MVP. Target workflow sections include future capabilities; the current implementation snapshot in section 9 distinguishes completed work. Verify the repository before making changes.

## 1. Product vision

Reprooo is an AI-assisted requirements refinement tool. It transforms loosely written business requests, copied discussions, and other context into structured, testable Jira task drafts. It identifies gaps, ambiguities, and contradictions, asks targeted questions, and incrementally improves the draft with the user.

The goal is to involve QA at the beginning of development rather than after implementation. A refined task should give developers and testers a shared understanding of intended behavior, constraints, and acceptance criteria.

**Not the current product goal:** converting Slack threads into bug reports. Slack text is merely one possible input source. The product is not limited to bugs.

## 2. Problem and users

**Problem:** Incoming business tasks often omit details needed to implement and test them. QA discovers missing requirements too late, triggering extra discussions and rework. Teams lack a consistent readiness checklist and reusable context about their systems and prior decisions.

**Primary users:** project/delivery managers, business analysts, QA engineers/leads, and developers.

**Desired shift:** QA can review requirements and prepare tests alongside development. The tool raises meaningful questions early; it does not make business decisions on behalf of stakeholders.

## 3. MVP user journey

1. **Enter context.** The user pastes a request, discussion, or other text into a field labeled **Context** (replacing **Thread text**). Preserve the existing masking presets/chips. Team/service selectors and attached specifications are desirable extensions, subject to scope and safety decisions below.
2. **Mask and review.** The backend masks sensitive information. The user reviews the masked content before any model request. Passwords, tokens, and other secrets must not be recoverable into final task drafts.
3. **Generate first draft.** The LLM produces a structured general-purpose Jira task draft based on supplied, permitted context. It also identifies missing or contradictory requirements and generates prioritized clarification questions.
4. **Refine iteratively.** The user answers questions and/or edits the draft. Each subsequent iteration updates only relevant portions of the existing draft, preserving previously agreed content. The user can run as many iterations as needed.
5. **Finalize.** The user reviews the resulting task, remaining open questions, and readiness indication. The user—not the model—decides whether to finalize.
6. **Mock Jira submission.** The app simulates submitting the task to Jira, displays a clear success result, then returns to a clean initial form after the user has had a chance to see the result. Reset context, selected optional filters, draft, and session state.

### Demo acceptance scenario

A user pastes an underspecified request such as: “Add a button to send a test Slack message from the service settings.” The system masks sensitive details, drafts a task, asks meaningful questions (e.g. who may use the button, where it belongs, what happens on success or failure), incorporates answers without overwriting agreed requirements, produces testable acceptance criteria, and submits the result to a mock Jira board.

The model must **not** invent unconfirmed choices such as button color, position, debounce interval, or access permissions. It should distinguish important decisions from optional implementation details.

## 4. Approved draft structure — requirements v1

The backend-owned registry defines one active template: mode `requirements`, version
`1`. The ordered plain-text string-map fields are:

| Field ID | UI label | Required for submission |
| --- | --- | --- |
| `summary` | Краткое описание | Yes |
| `business_context` | Бизнес-контекст | Yes |
| `requirements` | Требования | No |
| `acceptance_criteria` | Критерии приёмки | Yes |
| `open_questions` | Открытые вопросы | No |

Business Context captures motivation, stakeholders, and expected value. Requirements
can include functional behavior, constraints, dependencies, edge cases, and error
handling where supplied. A user story can be expressed in these sections when useful;
it is not a separate mandatory field. Acceptance Criteria contains observable,
testable conditions; Open Questions retains unresolved information. Both remain
plain text, optionally with newline-separated bullets, not structured objects.

Only Summary, Business Context, and Acceptance Criteria are mandatory for submission.
Generation may leave them empty and reports `missing_fields`; it must not invent
business decisions or use placeholders such as `TBD` to appear complete. This is a
structural submission check, not the future advisory readiness assessment.

The generator returns `template: {"mode": "requirements", "version": 1}` alongside
`draft` and `missing_fields`. Submission must include the same supported reference.
Unknown fields and non-string values are rejected; missing allowed fields normalize
to empty strings in configured order. Submitted required values must contain
non-whitespace text. Client-provided template definitions are forbidden.

This contract intentionally replaces the historical bug fields. `summary` is the
internal ID and Jira Summary mapping; `title` and `bug` are not compatibility aliases.
Frontend and backend must migrate together; reload and regenerate old browser drafts.
No database migration, automatic conversion, or historical template storage is needed.

The current offline stub copies the first nonempty sanitized input line to `summary`
and all sanitized input to `requirements`. It leaves Business Context, Acceptance
Criteria, and Open Questions empty. Task 5 additionally asks literal questions for empty required fields and proposes
copying explicit answers into empty sections. Semantic reasoning requires OpenAI mode.

## 5. Completeness analysis and questions

The analyzer should inspect the draft and available context for ambiguity, missing behavior, contradictions, and testability. Classify questions as:

- **Blocking:** essential to implement or verify the intended behavior unambiguously.
- **Recommended:** valuable to clarify, but not necessarily blocking.
- **Optional:** potential improvement or implementation detail that need not block the task.

Question states should support at least **unanswered**, **answered**, **not applicable**, **left to implementation**, and **needs stakeholder decision**. Avoid asking the same resolved question again without new evidence. Explain why each important question matters; do not overwhelm the user with speculative questions.

A readiness indication (e.g. **Ready for Development** / **Needs Clarification**) is advisory, not a hard gate. Explicitly retain unresolved questions when finalizing.

## 6. Incremental refinement — mandatory design decision

**Do not regenerate the entire draft from scratch at every iteration.** Preserve existing user-approved requirements and update only fields affected by new answers or edits.

Suggested implementation contract:

1. Keep a structured canonical draft in session state.
2. Treat explicit user edits and accepted decisions as authoritative.
3. The LLM returns **proposed structured changes** and an updated set of clarification questions, rather than a completely replaced document.
4. The orchestrator validates proposed changes and applies only permitted updates.
5. If new information contradicts an agreed requirement, flag the conflict and ask for confirmation; do not silently overwrite it.
6. Retain enough per-session version/change history to explain what changed and why. For MVP, in-memory storage is acceptable if clearly limited to a single-process demo.
7. Keep iteration count under user control; no arbitrary forced number of rounds.

The exact patch schema and approval UX should be proposed after inspecting the current code, not assumed to exist already.

## 7. Sensitive data and documentation

- Preserve the existing configurable masking chips and user-facing preview.
- **Never send raw secrets, tokens, passwords, or disallowed personal/internal data to the LLM.**
- Secrets must be irreversibly removed/redacted for generated output; reversible placeholders for other categories require a deliberate, safe restoration policy.
- Recheck user-edited masked text before model submission; editing must not bypass protections.
- Treat model output and attached documents as untrusted input. Validate structured output and avoid logging sensitive values.
- For demos, prefer synthetic tasks and synthetic documentation rather than real confidential company material.

**Documentation context:** The long-term idea is a team/service-scoped knowledge base of specifications and prior agreements. Documents may be stored without sending them wholesale to the LLM; however, any content the model must use has to be retrieved and selectively supplied after appropriate sanitization and access checks. Storage alone does not give the model knowledge of the document.

**MVP scope decision:** Support for uploading a specification is desirable but not required for the core end-to-end demo. Do not implement a full database, document retrieval system, or automated source-code ingestion unless explicitly prioritized later. Team/service chips may initially be static UI metadata or deferred.

## 8. Architecture and implementation constraints

Keep the existing modular architecture and current UI style as much as possible:

- **Frontend:** React, Mantine, Vite; preserve the dark interface, step-based flow, and masking presets.
- **Backend:** Python, FastAPI, Pydantic.
- **Orchestrator:** coordinate masking, session state, LLM calls, draft updates, validation, and mock submission.
- **LLM connector:** integrate the existing OpenAI client into the active flow instead of leaving draft generation stubbed.
- **Template configuration:** replace the bug-only output contract with a general requirements-task template.
- **Completeness analyzer:** separate logical responsibility from draft generation, even if both use the same provider/model.
- **Session store:** support draft state, question state, and refinement history with a documented TTL.
- **Jira adapter:** remain mock-only for MVP; no real Jira credentials or network writes.

Avoid broad rewrites, new infrastructure, or unrelated refactoring. Preserve working interfaces where feasible; explicitly plan and coordinate necessary API/schema changes between frontend and backend.

## 9. Historical baseline and current implementation

### Historical audit before Tasks 1–3

The earlier audit reported:

- Existing text input, configurable masking preview, editable draft UI, and mock ticket creation.
- An active deterministic stub in `backend/app/llm_client.py`; `backend/app/openai_client.py` exists but is not wired into the flow.
- A bug-only template and basic required-field emptiness checks.
- No clarification-question generation or iterative draft refinement in the active flow.
- In-memory session/ticket storage and no automated tests found.
- Important defects/risks: secrets restored into drafts, unvalidated session ID on ticket creation, placeholder collisions, and edits potentially bypassing masking before future LLM calls.

These findings describe the historical prototype, not the current implementation.

### Current implementation after Tasks 1–5

- Task 1 added irreversible supported-secret redaction, edited-preview rechecking,
  safe processing/output boundaries, and synthetic regression tests. No original
  masking values are retained or restored at this stage. Selective restoration
  remains deferred to an explicitly approved final-review policy and confirmation.
- Task 2 added sliding 900-second TTL, pre-worker API admission/reservations,
  atomic session operations, cancellation ownership, and periodic expiry cleanup.
- Task 3 replaces the bug structure with requirements v1 above, shared structural
  normalization/validation, explicit template references, general-purpose editor
  fields, and mock Jira mapping from `summary`. Failed template loading retains
  the generated draft for retry without automatic regeneration or a new session.
- Task 4 adds explicitly configured `LLM_PROVIDER=stub|openai` generation; stub
  remains the offline default. OpenAI uses sanitized context, strict requirements-v1
  Structured Outputs, a 60-second SDK timeout, zero automatic retries, and no
  fallback. Configuration is validated at application initialization. Safe 502/504
  errors preserve retry behavior and existing session semantics.
- Task 5 adds session-owned sanitized drafts/revisions, 0–5 gap questions,
  dispositions, targeted proposals, independent decisions and rejection protection.
  Manual edits synchronize only on explicit operations. Recovery and replay use the
  existing transactional session ownership. Jira open-question additions require
  explicit confirmation. Generation still starts with empty `open_questions`.
- Final section approvals (Task 6), persistent history and real Jira integration
  remain unimplemented. The offline stub remains intentionally non-semantic.

The rest of this document continues to define the target MVP. Current structural
checks and free-text Open Questions do not implement the future analysis/question
state model. Verify source code and tests before making implementation claims.

## 10. MVP priorities

### Must have for demo

1. Universal context input and preserved masking workflow.
2. Correct secret redaction and safety checks before LLM calls.
3. Real OpenAI-powered structured first draft.
4. General-purpose Jira task template with testable acceptance criteria.
5. Meaningful completeness analysis and clarification questions.
6. Incremental user-driven refinement without silent changes to agreed requirements.
7. Final review, mock Jira submission, and clean reset.
8. Focused verification/tests for masking, draft updates, and the core user flow.

### Nice to have if time permits

- Upload one sanitized specification for additional context.
- Team/service selectors (possibly static for demo).
- Improved visual diff/history of draft iterations.

### Out of scope for this MVP

- Live Slack API ingestion.
- Real Jira creation.
- Full knowledge-base database, RAG, or automatic document syncing.
- Automated PR-triggered repository specification generation.
- Production authentication, multi-user persistence, and deployment infrastructure.
- Multiple specialized task templates unless explicitly prioritized.

## 11. Future product direction

Later versions may support team/service-specific context, reusable rules and past clarifications, integration with Jira/Confluence/GitHub/GitLab, and specifications saved to repositories when pull requests are opened. Business intent should remain visible in Jira; technical implementation specifications can live with code. These are future directions, not current MVP commitments.

## 12. Instructions for the next Codex planning task

1. Read this document and inspect the actual repository.
2. Identify the smallest set of changes needed to achieve the MVP scenario.
3. Map each change to existing modules and frontend screens.
4. Identify unresolved product decisions and ask before choosing behavior.
5. Propose an ordered, small-step implementation plan with tests and demo checkpoints.
6. **Do not modify code until the plan is reviewed and approved.**
