# Reprooo Product Roadmap

## Purpose and planning horizons

Reprooo helps PMs, analysts, QA engineers, and developers turn incomplete task descriptions into structured requirements through AI-assisted clarification and explicit human review. This roadmap proposes an evolution from the hackathon MVP to a secure, multi-team requirements management and refinement tool.

This is a planning document, not a delivery commitment. Horizons express relative priority and maturity, not dates, estimates, release versions, or authorization to implement. Dependencies are entry conditions, not promises that every initiative in one horizon must finish before another can begin. All capabilities below the baseline are proposed unless explicitly identified as existing.

| Horizon | Focus | Initiatives |
| --- | --- | --- |
| **Now** | Improve the MVP and establish operational and privacy foundations | Production Foundation & Observability; refinement safety and finalization reliability |
| **Next** | Enable durable, authorized workflows and controlled document input | Persistent Storage & Session Reliability; Identity, Authentication & Access Control; Service Tokens & Delegated Permissions; File Upload & Specification Processing |
| **Later** | Add permitted organizational context, production delivery integrations, and aggregated insights | Team-Aware Knowledge Base; Real Jira Integration; Knowledge Retrieval / RAG; Requirements Quality & Product Analytics |
| **Exploring** | Validate broader ideas before promoting them to the backlog | Outcome evaluation; additional integrations and assisted specification maintenance |

## Current MVP baseline — implemented

The [README](../README.md), [technical overview](TECHNICAL_OVERVIEW_RU.md), and [defense cheatsheet](DEFENSE_CHEATSHEET_RU.md), checked against the current source, establish this baseline:

- React, Mantine, and Vite provide a step-based interface. FastAPI and Pydantic support separate masking, orchestration, refinement, validation, session, LLM, and mock adapter responsibilities.
- Users paste context, review configurable masking, and generate a five-section requirements draft. OpenAI mode uses Structured Outputs; the offline stub exercises workflow without semantic analysis. Gap analysis is a separate explicit action returning up to five prioritized questions.
- A session-owned canonical draft, revisions, question history, conflict flags, and individually accepted or rejected section proposals support incremental refinement. Manual edits synchronize on explicit operations. Final review supports copy, Markdown export, Mock Jira submission, and reset. Unresolved questions do not block finalization; inclusion in final text is explicit.
- Supported secrets are irreversibly redacted, edited content is rechecked before model calls, and original values are not restored. Rule-based masking is incomplete by nature; public demonstrations require synthetic data. Raw input reaches the backend before masking.
- Sessions, history, mock tickets, and usage controls are in process memory. Sessions have a sliding 900-second TTL after successful session-changing operations; restart loses state and browser reload does not automatically restore work. The deployment model requires one backend process and instance.
- Existing safeguards include request and resource limits, bounded paid dispatches, concurrency controls, conservative process-local budget reservations, and restricted diagnostic logging. Reservations are not actual billing visibility or a durable spending guarantee. The health endpoint checks the API handler, not OpenAI availability.
- There are no accounts, team permissions, persistent storage, file uploads, knowledge base, RAG, or real Jira/Slack integrations. The final snapshot is browser-owned; the API does not require proof of final review. Mock submission consumes the session, and a lost response cannot be recovered through a ticket lookup API.

Vercel and Render hosting are documented, but live deployment settings have not been independently verified. The [project context](PROJECT_CONTEXT.md) contains target requirements and historical snapshots; its closing statement treating question analysis/state as future work is stale. The technical overview's closing critique of README generation/iteration wording also predates the current README, which explicitly describes separate analysis and bounded usage. These older statements do not redefine the implemented baseline above.

## Now

### 1. Production Foundation & Observability

**Problem / motivation:** Minimal safe diagnostics and process-local controls support a demo but do not provide sufficient operational visibility or verified deployment behavior for sustained use.

**Proposed capability:** Improve structured application logging using an allowlist of event fields, safe correlation identifiers, and categorized failures. Add error monitoring and diagnostics, API latency and error-rate metrics, LLM dispatch/token usage metrics where available, and usage and cost visibility that distinguishes estimates, reservations, and provider-reported usage. Monitor active/expired sessions, cleanup failures, memory, saturation, restarts, and quota denials. Verify deployment configuration, single-process operation, ingress/proxy trust, CORS, secret injection, and diagnostic behavior.

**Expected value:** Operators can diagnose failures, recognize capacity limits, and understand consumption without inspecting private task content.

**Technical dependencies:** A telemetry data policy; approved diagnostic fields, retention and access rules; deployment access for verification; evaluation of monitoring options. Preserve the existing modular application and safe logging boundaries.

**High-level acceptance criteria:**

- Controlled success, failure, timeout, throttling, and restart scenarios produce distinguishable events and operational metrics; deployment verification records expected and observed settings.
- Dashboards or reports expose latency, error rate, LLM usage/cost attribution, session/resource pressure, and reset limitations. Missing billing data is labeled rather than inferred as actual spend.
- Synthetic secret/content tests demonstrate that application, proxy, monitoring, and provider-client logs exclude raw tasks, prompts, extracted text, outputs, credentials, and placeholder mappings. Correlation does not expose session bearer identifiers.
- Health/readiness documentation distinguishes API liveness from dependency availability; diagnostic checks do not silently issue paid model calls.

These are **application metrics**, describing reliability and consumption. They do not measure requirements quality or prove product effectiveness; initiative 9 addresses those separately.

### Refinement safety and finalization reliability

**Problem / motivation:** Human review is central to the product, but the final snapshot is local to the browser and submission results are not recoverable after a lost response. Masking and semantic conflict detection also have known limits.

**Proposed capability:** Strengthen synthetic evaluation of masking, conflict handling, preservation of accepted edits, and resolved-question behavior. Propose a server-validated final-review record tied to a draft revision, with recoverable submission outcomes. Keep readiness advisory and unresolved questions visible; do not invent missing business decisions or silently regenerate agreed requirements.

**Expected value:** Users retain control and can distinguish approved content from suggestions, stale revisions, and uncertain submission outcomes.

**Technical dependencies:** Coordinated frontend/backend contract design; explicit finalization semantics; durable storage before restart-safe records or result recovery. Immediate evaluation and design can precede persistence.

**High-level acceptance criteria:** Synthetic scenarios cover contradictory answers, stale edits, rejected suggestions, and resolved questions. A proposed finalization contract rejects stale approvals and retains unresolved-question decisions. Once persistence is available, a lost response can be reconciled without creating another submission. Secrets remain irreversibly redacted; any future restoration of other categories requires a separately approved policy.

## Next

### 2. Persistent Storage & Session Reliability

**Problem / motivation:** Restarts erase work, and process-local state prevents reliable multi-user operation and horizontal scaling.

**Proposed capability:** Persist users, teams, document metadata, and relevant workflow state, including canonical drafts, revisions, question dispositions, proposal decisions, and submission outcomes. Evaluate **PostgreSQL as a candidate relational database, not an approved architecture decision**. Introduce session recovery, retention/deletion policies, migrations, and backups. Move quotas, budget coordination, and concurrency ownership out of process memory before scaling.

**Expected value:** Users can resume permitted work, while operators can recover data and enforce consistent resource limits.

**Technical dependencies:** Data classification and ownership model; authentication for protected recovery; transaction/concurrency design; persistence and shared-state decisions; retention and backup policy. Persist sanitized state rather than original masking values.

**High-level acceptance criteria:**

- A controlled restart preserves an authorized user's saved draft, revision, questions, and decisions; browser recovery clearly distinguishes saved state from unsynchronized edits.
- Migration and backup-restore rehearsals preserve workflow consistency. Deletion covers active records and defines treatment of backups and derived data.
- Before multiple instances are enabled, concurrent updates reject stale writes and shared quotas remain enforced across instances and restarts.

### 3. Identity, Authentication & Access Control

**Problem / motivation:** Possession of a session ID currently grants session access; private multi-team work requires verified identity and explicit authorization.

**Proposed capability:** Secure user authentication, with SSO/OIDC as a candidate approach. Propose Admin, Team Maintainer, Member, and Viewer roles, explicit document/team/administration permissions, membership management, and audit trails for access and administrative actions. These roles and the permission model are proposals, not finalized requirements; no implicit universal document access is assumed for administrators.

**Expected value:** Teams can collaborate with accountable access boundaries and controlled administration.

**Technical dependencies:** Identity provider evaluation; persistent identities/memberships; an approved permission matrix, including sharing and administrative powers; session security and audit retention rules.

**High-level acceptance criteria:** Every protected backend operation checks identity, action, and resource authorization. Tests cover permitted and denied access, membership removal, cross-team access, and guessed identifiers. Membership/role changes and relevant access attempts are auditable without task bodies or credentials. Authentication alone never grants access to private documents.

### 4. Service Tokens & Delegated Permissions

**Problem / motivation:** External integrations need bounded machine access distinct from interactive user login.

**Proposed capability:** Administrator-managed service tokens with scopes such as `documents:read`, `documents:write`, and team-specific permissions. Provide an administrative interface for creation, expiration, rotation, revocation, and usage inspection. Store high-entropy token verification material using appropriate one-way verification, rather than recoverable raw tokens; evaluate hashing/HMAC and key handling. Define the represented principal and delegation boundary separately from user authentication.

**Expected value:** Integrations receive only the authority required for their work, with accountable and revocable access.

**Technical dependencies:** Identity, approved resource policies, durable token metadata, secure issuance, and audit infrastructure. Outbound provider credentials are a separate credential-management concern.

**High-level acceptance criteria:**

- Service tokens must not bypass user permissions: effective authority is bounded by approved scopes, team/resource policies, and the represented user's or service principal's delegated permissions.
- Permissions must be checked server-side on every protected request; expired, revoked, out-of-scope, and cross-team requests are rejected in tests.
- Rotation and revocation have a documented enforcement window. Audits identify token records and actions without raw token values.
- Tokens must never be sent to the LLM, included in task content, or written to logs; synthetic boundary tests verify exclusion.

### 5. File Upload & Specification Processing

**Problem / motivation:** Copying context manually makes service specifications hard to reuse and trace, while untrusted files create new security and privacy risks.

**Proposed capability:** Upload service specifications and technical documentation through a controlled ingestion flow. Supported formats remain to be determined. Introduce secure parsing/text extraction, file-size and processing limits, versioning and metadata, and explicit retention/deletion policies. Evaluate object storage for original files. Separate storage and extraction from external model processing: **uploaded files must not automatically be sent to OpenAI**. Users review sanitized extracted content before explicitly choosing it as model context.

**Expected value:** Users can reuse traceable documentation while controlling which information leaves the application.

**Technical dependencies:** Authorized document ownership, persistent metadata, storage choice, isolated/bounded parsing, approved format allowlist, masking policy, and deletion rules for originals, extracts, and versions. Evaluate malicious documents, parser vulnerabilities, embedded instructions/prompt injection, and decompression/resource exhaustion.

**High-level acceptance criteria:** Unsupported, oversized, malformed, and malicious test files fail safely within configured limits. Upload/extraction alone triggers no model call. Extraction records its source/version and exposes sanitized text for review; every subsequent model request rechecks edited content. Document instructions cannot grant permissions or trigger tools. Deletion behavior is documented and verified for originals and derived content.

## Later

### 6. Team-Aware Knowledge Base

**Problem / motivation:** Requirements lack reusable service context, but organizational knowledge must not become accessible merely through UI selection.

**Proposed capability:** Introduce initial team contexts **Frontend, Blockchain, Exchange, and Core**, subject to validating actual ownership. Support team selection, team-to-service mappings, specifications linked to teams/services, and team-specific requirements templates where justified. Automatically retrieve relevant permitted documentation within an explicitly enabled knowledge-assisted workflow, with source references in generated context. Enforce access at team, service, and document levels.

Selecting a team must not automatically grant access to its private documents. Cross-team access must be explicitly configurable: for example, Frontend may access approved public API contracts of Core without accessing Core's internal architecture.

**Expected value:** Users receive relevant shared context without losing ownership boundaries or source traceability.

**Technical dependencies:** Identity and resource authorization; persistent document/version metadata; secure ingestion; permission-aware retrieval before automatic private-context assembly. Cataloging and manual linking can precede advanced retrieval.

**High-level acceptance criteria:** Team selection changes context without changing permissions. Tests cover service/document restrictions and the Frontend/Core sharing example. Retrieved context identifies permitted source versions, and revoked documents are excluded. Any specialized template has a documented use case and preserves explicit review and canonical draft updates.

### 7. Real Jira Integration

**Problem / motivation:** Mock submission demonstrates the workflow but cannot deliver approved requirements into a production tracker.

**Proposed capability:** Secure Jira authentication and authorization; project/issue-type selection; mapping requirements to Jira fields; required-field validation; actual issue creation and the actual Jira issue URL. Include permission checks, categorized errors, safe retries, idempotency, duplicate prevention, and audit logging. Preserve Mock Jira for local testing and demos. Do not assume a particular organizational Jira authentication method or deployment model.

**Expected value:** Users can transfer the reviewed result into their permitted delivery workflow with a recoverable outcome.

**Technical dependencies:** Approved credential handling and Jira deployment/authentication choice; application and Jira permissions; field mapping; persistent submission/idempotency records; server-validated review tied to a revision.

**High-level acceptance criteria:** A separately authorized test environment verifies project/type selection, missing required fields, denied access, successful creation, and actual returned URLs. Lost responses and retry scenarios reconcile an existing outcome before another create attempt. Uncertain outcomes are visible; unsafe retries are blocked. Audits omit credentials and sensitive payloads, and mock mode remains usable without Jira credentials.

### 8. Knowledge Retrieval / RAG

**Problem / motivation:** Whole-document context is inefficient and can introduce irrelevant, stale, conflicting, or unauthorized information.

**Proposed capability:** Search relevant specification fragments using evaluated chunking and indexing, permission-aware retrieval, and source attribution. Track source versions and flag outdated or conflicting documentation for review. Evaluate simple metadata/keyword search before more complex approaches; a vector database is not assumed necessary from day one.

**Expected value:** Requirements refinement can use focused, explainable context that the requesting principal is permitted to see.

**Technical dependencies:** Secure versioned ingestion, ownership metadata, resource authorization, deletion propagation, masking, and a synthetic retrieval evaluation set. Permission-aware context assembly must precede cross-team RAG.

**High-level acceptance criteria:**

- Evaluation records relevance, source/version correctness, conflict handling, and permission isolation using predefined synthetic cases; release thresholds require future approval.
- Unauthorized fragments cannot reach model prompts, citations, caches, or user-visible search results. Permission changes and deletions invalidate derived access appropriately.
- Retrieved fragments are rechecked for sensitive data before model processing. Conflicting or stale sources remain identifiable rather than silently becoming authoritative requirements.

### 9. Requirements Quality & Product Analytics

**Problem / motivation:** Operational metrics cannot explain which requirements are missing, which suggestions help, or where refinement causes friction.

**Proposed capability:** Analyze common missing-requirement categories, clarification question patterns, accepted/rejected AI suggestions, and iterations required to finalize a draft. Offer team-level aggregated insights with privacy-preserving aggregation, restricted access, small-group protection, and defined retention. Later research may evaluate effects on rework or QA readiness.

**Expected value:** Teams can identify recurring gaps and prioritize template or workflow improvements using observable behavior.

**Technical dependencies:** Agreed event definitions and denominators, durable workflow events, team permissions, privacy/aggregation rules, and evaluation design. Useful collection requires a separate approved data policy; raw task content is not a default analytics input.

**High-level acceptance criteria:** Synthetic workflows reconcile recorded suggestion decisions and iteration counts with visible user actions. Reports distinguish abandonment, quota exhaustion, and finalization rather than counting them as equivalent outcomes. Aggregation suppresses disallowed small groups and prevents cross-team disclosure. Reports state that acceptance rates and iteration counts do not directly prove improved development performance or causal reductions in rework.

## Exploring

### Outcome evaluation and broader workflow assistance

**Problem / motivation:** Product usage alone cannot establish improvements in delivery outcomes, and broader integration demand remains unvalidated.

**Proposed capability:** Explore consent-based studies of rework/QA readiness, additional documentation or repository integrations, and assisted specification maintenance. PR-triggered specification proposals and reusable historical clarifications are hypotheses, not approved features or autonomous updates.

**Expected value:** Establish whether these extensions solve recurring user problems before expanding product scope.

**Technical dependencies:** User research, approved study definitions and data handling, reliable analytics, source permissions, credential management, provenance, and human review. Repository ingestion or external integrations require separate authorization.

**High-level acceptance criteria:** Each experiment states its user problem, permitted data, evaluation method, and evidence needed to advance or stop. Findings distinguish association from causation. Generated specification changes remain proposals; no experiment bypasses access controls, sanitization, or explicit acceptance.

## Major architectural dependencies

| Prerequisite | Enables / constrains | Readiness evidence |
| --- | --- | --- |
| Safe telemetry policy and verified deployment | Operational diagnosis and controlled rollout | Synthetic leak checks and recorded deployment verification |
| Persistent storage and lifecycle policy | Durable sessions, documents, review/submission records, and audit history | Restart recovery, migration, restore, and deletion exercises |
| Authentication and resource authorization | Private multi-team knowledge access and delegated tokens | Backend permission matrix with allowed/denied tests |
| Secure parsing, masking, and sanitized-content review | Document-assisted AI generation | Bounded parser tests and no model dispatch before explicit selection/review |
| Permission-aware retrieval and source lifecycle | Automatic private context and cross-team RAG | Isolation, revocation/deletion, and provenance tests |
| Credential handling, dual permission checks, and durable idempotency | Production Jira integration | Authorized test integration and ambiguous-outcome recovery |
| Shared workflow state, locks/concurrency ownership, quotas, and budget coordination | Horizontal scaling | Multi-instance consistency and quota tests before adding workers |
| Defined events and privacy-preserving aggregation | Team product analytics and possible outcome research | Reconciled events, access checks, and approved evaluation design |

## Open Architectural Decisions

All choices below remain unresolved. Candidate technologies are evaluation options, not approved selections.

| Decision | What needs evaluation |
| --- | --- |
| PostgreSQL and persistence architecture | Relational fit, transactions/revisions, session expiry versus durable drafts, shared coordination needs, operations, migrations, and recovery |
| Object storage provider | Deployment fit, encryption, access isolation, versioning, retention/deletion, and backup behavior for originals |
| Authentication / SSO provider | Organizational identity compatibility, OIDC support, provisioning/deprovisioning, session security, and administrative ownership |
| RBAC versus granular resource policies | Whether proposed roles suffice; team/service/document exceptions, cross-team contracts, inheritance, and administrator access boundaries |
| Service token lifecycle and management | Represented principals, delegation limits, scope design, one-way verification, expiration, rotation, revocation timing, and audit ownership |
| Document formats and processing pipeline | User demand, parser safety, extraction fidelity, isolation, resource limits, review UX, and derived-data lifecycle |
| Search / RAG approach | Metadata/keyword baseline, chunking, index or embedding needs, quality evidence, source freshness, permissions, and deletion propagation |
| Jira authentication and deployment model | Actual Jira environment, supported organization-approved credentials, user versus service delegation, custom fields, and retry/reconciliation behavior |
| Data retention and deletion | Separate lifecycles for drafts, originals, extracts, indexes, telemetry, audits, and backups; ownership and deletion obligations |
| Monitoring and observability stack | Safe instrumentation, deployment compatibility, restricted diagnostic access, retention, alerting, and usable cost attribution |

## Backlog planning guardrails

Before promoting an initiative into implementation, agree its smallest reviewable scope, permissions and data boundaries, dependency evidence, and acceptance checks. Preserve modular responsibilities, coordinated frontend/backend contracts, canonical incremental refinement, explicit human decisions, and unresolved questions. Do not treat readiness assessments as automatic approval or resolve missing business requirements through model invention.

This roadmap adds no application functionality and selects no final architecture. Live integrations, deployment changes, data migration, and external processing each require separately approved work.
