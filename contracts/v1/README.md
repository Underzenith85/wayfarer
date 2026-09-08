# Wayfarer HTTP contract v1

Issue #47, parent #46. `openapi.json` and `schemas.json` are the source of truth
for the frozen playable HTTP slice. Freeze takes effect when this PR is reviewed
and merged. `proposed.md` defines later interfaces without promising compatibility.
`examples.json` binds payloads to actual operation IDs and response statuses.

This is a **target contract**, not a description of deployed routes. #50 implements
backend conformance; #48 specifies live event envelopes and WebSocket transport;
#49 generates the client, mocks and breaking-change gates. This PR does not modify
runtime behavior. UI implementations may use the frozen payloads immediately.

## Validate

From the repository root:

```bash
uv sync --frozen
uv run --frozen python scripts/validate_contracts.py
uv run --frozen pytest tests/test_contracts.py --no-cov
```

Validation uses OpenAPI 3.1 and JSON Schema 2020-12 validators, resolves only local
references, checks each operation's auth policy and fixture coverage, and rejects
inconsistent action states. No API server or model credentials are required.
See [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html) and
[JSON Schema 2020-12](https://json-schema.org/draft/2020-12/json-schema-core).

## Frozen slice and access matrix

All operations are relative to `/api/v1`, require bearer authentication, and return
`application/json`. HTTPS is mandatory except loopback development. Existing
server-configured opaque tokens can provision development identities; token
issuance/login/refresh is outside this slice. Never put tokens in URLs. The browser
must clear all user-scoped state on logout or principal change. No browser API key
can authorize a model provider or reveal provider credentials.

| Operations | Authorization and projection |
|---|---|
| `getMe`, `listCampaigns` | Authenticated self; campaigns with current membership only |
| `getCampaign`, `getCurrentSession` | Member; perspective-filtered summaries, objectives and time |
| `listMembers` | Member; roster is public within campaign; other actor assignments omitted as empty arrays unless visible |
| `listCharacters`, `getCharacter`, `getInventory` | Controlled actors or explicit GM role; spectators receive no detailed sheets |
| `listScenes`, `getScene` | Authorized scenes and observations only; GM can inspect all scenes |
| `submitAction`, `clarifyAction`, `cancelAction` | Player role, controlled actor, current authorized scene; GM role alone cannot act as a player |
| `listActions`, `getAction` | Own actor actions in authorized scene, or explicit GM inspection; a committed action is listed under the scene it was taken in and under the scene its receipt left the actor in, so a journey stays readable from its destination |
| `createInvitation` | GM; invite player/spectator only, never grant GM or character control |
| `redeemInvitation` | Authenticated holder of valid single-use token; no existing-role escalation |

Every operation also declares `x-authorization` in OpenAPI. Missing/invalid token
returns 401 and `WWW-Authenticate: Bearer`. Unknown and inaccessible resources
share 404 without names, versions or hidden IDs. 403 is reserved for forbidden
operations on resources already visible to the caller. Recheck nested target/item
IDs against campaign, ownership, reachability and scene visibility. A request body
cannot select its principal or grant permission. Reauthorize before returning
stored action results and invitation retry payloads. Search, recaps, events and
cache keys must use the same policy. Reunion does not automatically share secrets.

GM is an explicitly privileged human/service role, not a credential given to the
LLM. A provider receives bounded permitted context through engine orchestration.

## Representation conventions

- IDs are opaque, case-sensitive, URL-safe strings, 1–100 characters. Do not infer
  location, chronology or authority from IDs. Client command IDs are UUIDs.
- Version tokens are opaque strings scoped to the caller-visible resource. They
  are not global event counters. Hidden subgroup changes must not expose their
  frequency through version increments. #50/#45 must map underlying campaign
  revisions to permitted concurrency tokens and retry internally where safe.
- `updated_at`/`created_at` are wall-clock UTC RFC 3339 strings ending in `Z`.
  `GameTime` separately represents nonnegative integer simulation ticks and the
  campaign-pinned tick duration in milliseconds. It never advances from UI time.
  Scene time is the authorized scene's time; public campaign time must respect
  shared-time/visibility policy rather than reveal hidden group progress.
- Integers stay within JavaScript's safe range. Inventory weight is integer grams;
  no floating-point currency. Currency/money semantics remain proposed.
- Required nullable fields must be present with null when absent (e.g. no current
  session summary, no containing item, last page cursor). Optional fields are
  omitted when inapplicable. Unknown properties are rejected by the v1 schemas.
- Confiscated inventory may retain the character's known item identity, but may
  not expose an unknown custodian/container/location. Use null container ID.
- List limit defaults to 50, maximum 100. Order by stable ID ascending. Cursors
  are opaque, bound to principal/campaign/filter and a permission-filtered snapshot,
  valid for 15 minutes. `next_cursor: null` means end. Recheck current permissions
  on every page; a revoked snapshot returns 410 `cursor_expired`, with no hidden
  data. Malformed/filter-mismatched cursor returns 400 `invalid_cursor`. Restart
  pagination on 410; do not combine pages across fresh snapshots. Inventory is a
  complete bounded projection, max 1,000 visible item stacks; deployments must
  enforce that limit until a paginated inventory contract is reviewed.
- All responses, including errors, include server-generated UUID `X-Request-ID`
  and `Cache-Control: no-store`. Error `request_id` matches the header. Ignore
  untrusted incoming request IDs; no secrets or hidden state in messages.
- JSON bodies max 32,000 bytes, enforced on decoded/chunked bodies as well as
  Content-Length. Unsupported media type is 415, oversize is 413, malformed or
  schema-invalid input is 400. Query/path values are validated too.

## Commands, concurrency and retries

HTTP is intent submission and resource reads. There is no HP/inventory patch API.
Structured controls and text enter the same authoritative interpretation,
validation and resolution pipeline. A natural-language proposal cannot supply
rolls, outcomes, costs or permission. Unknown mechanics return unsupported_action
or later reviewed adjudication capabilities, never made-up success. The frozen
structured intent set is inspect, move, use_item, wait and question; free text may
request more but capabilities/engine validation determine support. Attack/equip/
transfer UI controls await proposed contracts and their engine integrations.

Every POST contains `command_id`. Scope is `(principal_id, command_id)` across all
operations; fingerprint includes method, path and schema-normalized JSON. Validate
auth and body, then atomically reserve the ID before accepting work. Persist the
reservation and request fingerprint with the action/operation receipt; concurrent
identical submissions have one winner and receive the same accepted identity.

Retain fingerprints and receipt identity for the campaign lifetime (including
archive). Command receipts and their results are not pruned in v1; future deletion
policy must preserve non-reexecution guarantees through a reviewed contract. For invitation
creation/redemption, retain receipts for the resulting campaign lifetime too.
The same ID with different body/path returns 409 `idempotency_conflict`.

Exact authenticated retries return the same HTTP success status and action ID,
with the latest authorized action state (not necessarily byte-identical JSON),
without re-execution. Invitation retries return the original receipt only to the
original authorized principal; redact nothing into a misleading successful result.
Expired/revoked invitation tokens cannot be newly redeemed. A lost successful
redemption response is retrievable by exact retry after token consumption; current
membership is still required. Existing-member redemption consumes no invitation,
returns the current membership and never changes role or controlled actors.

Preacceptance 4xx/429/503 do not reserve command IDs. Once 202 is accepted,
business rejection is persisted as `ActionRejected`; it is not a transport retry.
On timeout/500/503 with uncertain acceptance, retry the exact same command ID and
body. Never automatically create a new ID. Back off for 429/503 using required
integer-seconds `Retry-After`; `retryable` is true only for those errors in v1.
A 500 is not a promise of safe automatic retry; recover via the same command ID.

Actions require scene and character versions; use_item additionally requires the
inventory version (enforced semantically and in schema). Clarification uses a new
command ID, original action ID, current clarification ID/action version, and fresh
resource versions. If text interpretation discovers inventory consumption without
a submitted inventory precondition, return needs_clarification and require a
fresh inventory version with the answer before resolving. Never infer consent
for a newly observed inventory state. Cancellation uses a new ID and action version. Invitation
creation checks the caller's membership version. A mismatched required visible
version returns 409 `stale_version` without committing gameplay; refetch authorized
state and let the player reconsider before a new command ID. Do not expose a
hidden resource's current version in the error.

Acceptance is not a gameplay commit. The engine revalidates visibility, ownership,
versions and feasibility at resolution under its transaction. If invalidated after
acceptance, persist `rejected` with the relevant error, without spending resources.
A successful dice failure is still `succeeded`: the action executed and has an
authoritative resolution, even when the character's check failed.

## Action lifecycle

| From | Allowed next states | Meaning |
|---|---|---|
| submitted | needs_clarification, resolving, rejected, cancelled | Accepted and persisted; no gameplay effect yet |
| needs_clarification | submitted, rejected, cancelled | Valid answer resumes the same action; no fresh action identity |
| resolving | succeeded, rejected | Engine owns resolution; cancellation no longer allowed |
| succeeded, rejected, cancelled | none | Terminal, immutable gameplay outcome |

Action versions increment for each visible transition. 202 may already contain a
terminal state if processing completed quickly. `listActions` returns ascending
creation order, with the action id only as a tiebreak, and pagination preserves
that order across cursors. `getAction`/`listActions` provide
polling recovery without waiting for #48; poll no faster than once per second,
back off while inactive, and obey 429. Only a needs_clarification record contains
`clarification`; only succeeded contains `resolution`; only rejected contains
`error`. The schema enforces these unions. No generic mutable status string.

Cancellation is atomic against transition into resolving. Cancelling submitted or
needs_clarification returns the cancelled action; other states return 409
`invalid_transition`. An exact retry of an accepted cancellation succeeds. A new
cancellation command on a terminal action is invalid. Narration interruption is
separate (#48/#24) and never undoes committed gameplay or another scene's action.
Narration failure cannot change succeeded to rejected. Structured committed
results remain readable even if the provider is unavailable.

## Capability and evolution policy

A campaign's `capabilities` list identifies implemented action/features, e.g.
`actions.text`, `actions.inspect`, `actions.use_item`. An advertised capability
must conform; absent capabilities must not show active controls. An entry may be
scoped to one target as `actions.<kind>:<target_id>`, which narrows the plain
capability to the named, already-visible targets; a client that sees no scoped
entry for a kind uses the plain capability alone. Scoped entries are ordinary
capability strings, so this adds no field and no new schema. #50 must deliver
the frozen slice before claiming v1 support. Schemas can be consumed before then
through #49 fixtures. Do not silently alias the demo endpoints to the new API.

This directory is versioned as 1.0.0. Changes to frozen operation semantics, required
fields, enums or closed unions require reviewed contract/client/fixture changes;
wire-breaking changes require a new major API path. Documentation corrections may
be patch releases. Do not silently widen a closed union under existing clients.
Proposed surfaces can change until promoted through review and validation. #49
adds automated diff checks; this issue already validates structure and examples.

## Current implementation inventory and gaps

Inspected main commit `9a56802a8fc6d579635b5fd8aed046930531396a`.

| Current source/route | Existing behavior | v1 gap / owner |
|---|---|---|
| `transport/campaign_api.py`: GET `/health` | Public health | Operational endpoint stays outside player v1 |
| GET `/campaigns/{cid}` | Bearer + member, revision/game_time/actor perspectives; GM world projection | Typed player DTOs, opaque scoped versions, character/inventory/scene reads: #50 |
| POST `/campaigns/{cid}/commands` | Typed `id`, actor_id, expected_revision; calls PlayService, returns projection | New envelope, result resource, async lifecycle, clarification/cancellation: #50/#20/#39 |
| GET `/campaigns/{cid}/events?after=N` | JSON polling of revision-based history; not a WebSocket stream | Scoped envelopes, opaque cursors, replay/revocation and transport: #48/#50/#45 |
| `orchestration/access.py` | gm/player/spectator, owned actor control; other actor outcomes redacted | Filter event metadata as well as outcomes, historical revocation tests, scene authorization: #45/#50 |
| `orchestration/play.py` | Transactional commands, duplicate lookup, feasibility, revision checks | Persist accepted action/clarification lifecycle; resource-token mapping: #50/#39 |
| `transport/http.py`: `/api/bootstrap`, campaigns, turn, validate, generate routes | Separate legacy local demo; not authenticated authoritative facade | Do not treat as production v1. Setup/generation adapters: #21/#22/#40/#50 |
| Character/compiler, resources, combat, scenes, advancement services | Domain implementations independent of HTTP | Typed projections and command adapters, not duplicated mechanics: #50 and respective engine owners |
| No principal/list/invite/session API | Membership records are seeded server-side | Self/list/invitations and current-session projection: #50/#40 |

Existing rate limiting can return early without normal headers; oversized requests
can bypass the structured domain error path; incoming request IDs are trusted.
#50 must normalize these boundaries. Current event history exposes global revision,
actor IDs/action kinds and projects historical states using current membership;
outcome redaction alone is not the v1 visibility guarantee. This contract does not
certify those paths as safe for split-party production play.

## Scoped live updates

The [live protocol](live-protocol.md) and `events.schema.json` define the companion
WebSocket message contract, including snapshots, replay, authorization changes and
provisional narration. Its executable fixtures supplement these HTTP examples;
runtime conformance remains #50/#45. Validate with
`uv run --frozen python -m scripts.validate_live_contracts`.
