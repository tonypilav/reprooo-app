# Public demo abuse protection — Task 7.3.2

## Scope and limits

This is a single-process, single-instance anonymous demo. No authentication,
Redis, database, persistent ledger, deployment, or real Jira/Slack integration.

| Resource | Ceiling |
| --- | --- |
| POST mask preview / session creation | 5 attempts / rolling 60 seconds / IP |
| POST generate, analyze, refine combined | 6 attempts / rolling 60 seconds / IP |
| Actual paid dispatches globally | 30 / rolling 60 seconds |
| OpenAI network calls | 2 concurrent, immediate rejection |
| Outstanding session operations | 16, including pending worker/session-lock work |
| Paid dispatches per session | 12, including failed attempts |
| Generation / analysis / refinement output tokens | 4096 / 2048 / 4096 |
| Serialized outbound content | 128 KiB UTF-8 including SDK-normalized schema/instructions |
| Issued placeholder metadata | 2000 entries/session, no active-entry eviction |
| Active rate-limit identities | 2048; expire after their buckets become empty |
| Conservative budget reservations | $4 / backend process lifetime |

Ceilings are fixed in source; model selection cannot bypass reviewed pricing.
Route throttles count malformed requests and masking-review responses. Successful
replays may consume a route attempt but never dispatch the model again. Only actual
OpenAI dispatch attempts consume the global paid-call window, session quota and
budget. Stub workflows do not consume paid quota. Decisions, final preparation,
export, mock Jira submission and deletion are not subject to LLM route throttles.

Admission and dispatch use short thread-safe critical sections; no global lock is
held over a network call. Running workers own permits until the SDK returns or
raises, even after HTTP cancellation. The 60-second SDK timeout is not a guarantee
that the provider stops processing a disconnected request. Pending work cancelled
before dispatch does not consume a paid reservation. No automatic retries.

Paid accounting belongs to the session entry, outside the transactional masking /
refinement working copy. It survives failure, revision rollback and regeneration.
A fresh session gets a fresh session quota but shares the same global budget.
Session cleanup releases the accounting object; no unbounded session-ID ledger.

## Conservative budget assumptions and limitations

Only `gpt-5.4-mini-2026-03-17` is approved. The ordinary standard-tier text prices
reviewed on 2026-11-12 are $0.75 / million input tokens and $4.50 / million output
tokens. Requests explicitly use `service_tier="default"`, reasoning effort `none`,
no tools, `store=False`, 60-second SDK timeout and `max_retries=0`.

References: [model and pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[Responses API output limit](https://developers.openai.com/api/reference/resources/responses/methods/create).

Before dispatch, reserve 150,000 input tokens plus the full output allowance:

- Generation/refinement: $0.112500 + $0.018432 = **$0.130932**.
- Analysis: $0.112500 + $0.009216 = **$0.121716**.

Use integer microdollars and atomic check/increment. Never refund a dispatched
reservation, even on timeout, cancellation, refusal, incomplete JSON, provider
failure, downstream validation failure or missing usage. Usage is not required to
keep protection working. Concurrent in-flight requests are already fully reserved.

The 150k input estimate conservatively treats each byte of the capped UTF-8 text
and normalized schema as potentially one input token, with roughly 19k additional
tokens reserved for model framing. This assumes the reviewed byte-based tokenizer,
text-only calls and current SDK serialization. It is an estimate, not measured
usage or an invoice guarantee. Changing model, schema transport, pricing, tool
configuration or service tier requires reviewing this profile. Unknown configured
models fail at startup and are rejected again before dispatch.

The $4 ceiling allows 30 generation-sized reservations or 32 analysis-sized
reservations, leaving approximately $1 against the intended $5 demo target for
uncertainty. Actual usage will typically be lower; do not refund the difference.
There is no guarantee that a long response fits the output cap. Incomplete,
refused or invalid structured output fails safely with the existing 502 contract,
no draft/revision commit, and no automatic retry. Output caps include reasoning.

**The ledger resets on every process restart. It does not guarantee $5 total
lifetime/demo spend.** Multiple instances, worker processes, restart loops and
other consumers of the key multiply spending exposure. Use exactly one process
and instance, a dedicated OpenAI project/key, and independent provider-side usage
monitoring. Provider budget alerts are not hard spending limits. Price changes,
taxes and usage outside this process are outside the accounting guarantee.

## Proxy identity and Render startup

Start from the repository root, with required environment configuration already
set (including APP_ENV=production and explicit HTTPS CORS origins):

```sh
backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port "$PORT" --workers 1 --no-proxy-headers --no-access-log --log-level warning
```

`--no-proxy-headers` is mandatory for this strategy: the application must see the
raw socket peer. Do not let Uvicorn rewrite it before the application's trusted-hop
resolver runs. `TRUSTED_PROXY_CIDRS` defaults to empty, so all forwarded headers
are ignored. Wildcards in that setting or FORWARDED_ALLOW_IPS fail startup (also
in development). CLI wildcard Uvicorn trust must never be enabled; the application
cannot recover the original socket peer after an upstream ASGI wrapper rewrites it.

When explicitly configured, the application accepts one bounded X-Forwarded-For
header only from a trusted socket peer and walks it right-to-left, stopping at the
first untrusted hop. Malformed, duplicate or oversized chains fall back to the
socket peer. Other forwarding headers never establish client identity. Identity
storage is bounded and does not evict active limits. No IP values are logged.

**Before enabling Render proxy trust**, verify the actual ingress proxy IP/CIDR
contract, whether XFF is appended/overwritten, and whether direct/untrusted ingress
is possible. Configure only verified ingress ranges, never Render outbound ranges
or a blanket private-network range. Test forged headers through the actual service.
Without verification, keep the default: users behind the same Render proxy may
share a restrictive IP bucket. This affects fairness, not global cost protection.
Do not deploy multiple workers or overlapping instances expecting shared limits.

## Errors, state and frontend behavior

Errors contain fixed codes/messages only. Retry-After is an integer number of
seconds and is exposed through CORS. No automatic client retry or resubmission.

| HTTP | Code | Guidance |
| --- | --- | --- |
| 429 | RATE_LIMITED | Retry after the indicated rolling-window delay |
| 503 | LLM_CAPACITY_EXHAUSTED | AI busy; retry manually after 2 seconds |
| 503 | OPERATION_CAPACITY_EXHAUSTED | Outstanding work full; retry after 2 seconds |
| 503 | RATE_LIMIT_CAPACITY_EXHAUSTED | Identity table full; retry after 60 seconds |
| 503 | DEMO_BUDGET_EXHAUSTED | AI allowance exhausted; continue reviewing/exporting |
| 409 | SESSION_LLM_LIMIT_REACHED | Session AI quota exhausted; continue reviewing/exporting |
| 409 | LLM_INPUT_LIMIT_REACHED | Reduce outbound content; no silent truncation |
| 409 | MASKING_LIMIT_REACHED | Existing metadata/state preserved |
| 503 | CAPACITY_EXHAUSTED | Existing session/mock-ticket storage error |
| 409 | REFINEMENT_LIMIT_REACHED | Existing retained-history/receipt error |

Missing paid account / unapproved runtime model also fail closed with safe 503
LLM_ACCOUNT_UNAVAILABLE / LLM_MODEL_NOT_APPROVED. These indicate server wiring or
configuration failures and receive a generic frontend error.

Russian UI mappings preserve user text, drafts, revisions and retry request IDs.
Existing 413 size errors also have a dedicated message. No API success-schema
migration. Reload old frontend bundles to receive the new messages.

Placeholder issuance stages all additions before merging. Excessive operations
reject atomically; old metadata is never silently removed from previews, revisions
or receipts. Issued metadata contains category labels only. No original values,
secret restoration or new reversible restoration policy is introduced.

## Production logging and residual risks

Application initialization disables Uvicorn access logging and OpenAI/HTTP transport
loggers. Use the startup flags above; do not re-enable debug logging or install
middleware that records bodies, headers, URLs or exception payloads. Uvicorn error
diagnostics are reduced to a fixed message with no exception text/traceback.
Application warnings retain fixed failure categories and counts only. Unexpected
processing failures use a fixed warning. This sacrifices traceback detail to avoid
sensitive exception data. No raw bodies, session IDs, IPs, authorization data,
prompts, placeholder maps, secrets or model output are logged by this code.

Check Render's platform-generated access/edge logs and retention separately before
public deployment; application logging settings cannot govern platform logs.
Connection floods, slow clients and distributed denial of service still need edge
controls. Anonymous clients can exhaust the shared allowance. NAT users share IP
limits. Readiness, revision protections, mock Jira and irreversible masking remain
unchanged. Unlimited refinement is a target requirement, qualified by this demo's
explicit paid-call and retained-state limits.


## Offline verification

Use only synthetic fixtures and mocked SDK / HTTP transports:

```sh
LLM_PROVIDER=stub PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend backend/.venv/bin/python -B -m unittest discover -s backend/tests -q
cd frontend
node --test src/*.test.js
npm run lint
VITE_API_BASE_URL=https://api.example.test npm run build
cd ..
git diff --check
```

The synthetic HTTPS URL is for build validation, not a deployed backend. Tests
cover concurrent HTTP/dispatch boundaries, pending and running cancellation,
rollback-independent quotas, exact outbound-byte boundaries, restart reset,
spoofed proxy chains, placeholder atomicity and sanitized logger descendants.
SDK schema-size accounting uses the pinned SDK's Structured Outputs normalizer;
review that integration when updating the SDK. No dependency versions were changed.
