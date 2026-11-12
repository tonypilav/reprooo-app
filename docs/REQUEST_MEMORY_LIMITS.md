# Request and memory limits — Task 7.3.1

These limits cover the single-process, anonymous hackathon demo. They do not add
rate limiting, model concurrency controls, authentication, or persistent storage.
Text budgets count Python Unicode characters; the HTTP budget counts received
bytes. JSON escaping and multibyte UTF-8 can make the HTTP budget more restrictive.

## Limits and rationale

| Resource | Maximum | Rationale |
| --- | --- | --- |
| HTTP request body | 262,144 bytes (256 KiB) | Bounds pre-parser buffering while allowing normal draft/answer/review envelopes. |
| Initial context | 20,000 characters | Several pages of context without arbitrarily large pasted documents. |
| Edited/masked preview | 40,000 characters | Allows UUID placeholder expansion. Excessive masking expansion is rejected before session creation or preview commit. |
| Draft | Five requirements-v1 fields; 20,000 characters per field; 50,000 total | Supports substantial technical requirements; shared bounds apply to user and generated drafts. Summary retains a generous budget for the offline stub. |
| Masking rule selections | 20 entries; 100 characters per key | Accommodates current chips with headroom. |
| Template mode | 100 characters | Bounds reference metadata; only the existing supported template is accepted. |
| Refinement answers | 20 per request; 4,000 characters each; 20,000 total | Supports answers to current and earlier questions without the former 100-answer envelope. |
| Questions per analysis | Five (unchanged) | Preserves focused clarification batches. |
| Question content | Text/rationale: 1,000 characters each; evidence: 2,000 (unchanged); retained answer: 4,000 | Bounds both generated content and retained answers. |
| Retained question history | 40 questions; 40,000 total characters across text, rationale, evidence and answer | Supports eight full five-question batches if aggregate/receipt budgets also fit. Resolved and superseded questions count; nothing is pruned. |
| Proposals | Five; replacement/base text: 20,000 each; reason: 1,000; five supporting question IDs (existing bounds) | Preserves targeted edits; retention additionally requires receipt space. |
| Review token | 196,608 characters | Replaces two million characters and reserves HTTP envelope headroom. |
| Request/question/proposal IDs | 100 characters (unchanged) | Preserves current client IDs and bounds metadata. |
| Replay ledger | 32 keyed request digests (unchanged) | Preserves bounded replay/revision protection. |
| Sessions | 100 retained entries | Allows headroom for 5–10 users and abandoned flows. |
| Mock Jira tickets | 100 retained tickets | Exceeds expected demo submissions without silent deletion. |

## Enforcement and atomicity

The ASGI middleware checks actual received bytes, including absent/inaccurate
Content-Length and streamed/chunked bodies, before appending to a buffer capped at
256 KiB. Routing, parsing and worker admission begin only after the complete body
fits. Oversized streams stop being consumed; the response is fixed and bodies are
not logged. CORS remains outside the limiter so rejection headers are preserved.
The HTTP server still owns the allocation of each delivered chunk.

Schemas reject oversized fields/aggregates before masking/model work. Shared draft
normalization also covers internal callers. Generated content is validated before
retention. Existing safe validation errors omit Pydantic input values and details.

Session creation performs expired-entry cleanup, capacity checking and insertion
under the registry lock. Cleanup never waits on per-session operation locks.
Previously admitted/pinned expired work still occupies capacity until released; it
cannot be evicted. Valid sessions are never silently evicted. Successful operations
retain the existing 900-second sliding TTL measured from completion; failure does
not refresh it.

History checks run within the existing per-session transaction/lock. Answer growth
is checked before provider work; new analysis stops at 40 questions. Generated
additions are checked before commit, so a rejected provider result may already have
incurred a request. Over-limit operations roll back manual edits, answers,
resolutions, supersession, proposals, section protections, revision, replay entries
and masking metadata together. Existing questions can still be answered and
proposals accepted/rejected at the question-count ceiling; preparation/submission
remain possible within aggregate limits.

Every retained refinement state must fit its signed receipt, accounting for UTF-8,
JSON and base64. Before issuing a masking-review receipt, the service validates the
sanitized review and checks its entire retry envelope using conservative
ASCII-escaped JSON. Individually valid large histories, drafts or proposals that
cannot fit together are rejected before commit. Unicode/escape-heavy combinations
may hit these byte budgets earlier; no unusable receipt is issued.

Mock Jira locks capacity checking, key allocation and insertion. Capacity rejection
appends no ticket and rolls back the enclosing single-use session operation,
preserving revision and expiry. A later successful retry consumes the session once;
concurrent duplicate submissions cannot append another ticket. Tickets have no
expiry/delete workflow: at capacity, a process restart is needed to retain more
(and also clears sessions).

## Configuration

Optional backend environment settings accept positive integers up to their safe
default/ceiling. Invalid values fail application initialization without echoing the
configured value. Omit settings for defaults.

| Setting | Default / ceiling |
| --- | --- |
| `MAX_ACTIVE_SESSIONS` | 100 |
| `MAX_MOCK_TICKETS` | 100 |
| `MAX_REFINEMENT_HISTORY_QUESTIONS` | 40 |

Other bounds are shared constants, avoiding independently adjustable budgets that
could contradict each other. No dependencies, migration, provider integration
changes or frontend changes are required.

## Error contracts and workflow effects

| HTTP | Code | Meaning |
| --- | --- | --- |
| 413 | `REQUEST_TOO_LARGE` | Body rejected before endpoint processing. |
| 422 | `INVALID_REQUEST` | Existing sanitized schema/structural validation response. |
| 503 | `CAPACITY_EXHAUSTED` | Session or ticket storage full; existing state preserved. |
| 409 | `REFINEMENT_LIMIT_REACHED` | History or review representation cannot fit; existing state preserved. |

The three new codes use the existing `detail: {code, message}` envelope. Current
frontend code uses its generic failure path and preserves content/session state.
A submission failure invalidates the local final snapshot through existing
behavior; prepare again before retrying. Dedicated capacity/size/history messages
require a separate frontend UX change. Existing session, revision-conflict, LLM
and incomplete-draft contracts remain unchanged.

The target MVP describes user-controlled iteration without a forced round count.
This demo imposes a retained-history budget, not a fixed round count. Refinement of
existing questions can continue within the budgets. Including many unresolved
questions during finalization can exceed a draft budget and require manual editing.

## Verification and remaining risks

Run synthetic offline checks from the repository root:

```sh
LLM_PROVIDER=stub PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend backend/.venv/bin/python -B -m unittest discover -s backend/tests -q
git diff --check
```

Tests cover byte/character/aggregate limits, streamed requests, safe errors,
concurrent creation at 99/100 sessions, concurrent cleanup with pinned expired
work, sliding TTL, history/receipt rollback and replay, Jira capacity and single-use
retries. Existing tests exercise the complete MVP path offline.

These bounds do not cap simultaneous requests, model calls or total spend. Existing
issued-placeholder metadata can grow across repeated successful sanitization within
a sliding-TTL session; this task leaves its retention policy unchanged. Server
buffering, slow clients and provider response buffering remain outside these
application limits. All retained state is process-local and lost on restart.

Manual demo: enter synthetic context, mask, generate, answer and accept a proposal,
prepare the final review, submit a mock ticket, then reset. Check generic errors
preserve edits using smaller storage settings in a separate local process.
