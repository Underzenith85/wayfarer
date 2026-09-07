# Frozen v1 runtime (#50)

`create_campaign_app` now serves the frozen `/api/v1` HTTP operations and
`/api/v1/live`. Older raw engine routes require explicit `legacy_routes=True`
for migration/testing and must not be exposed as part of a v1 deployment, because
their global revision/history payloads do not implement the frozen privacy contract.
The local demo
in `transport/http.py` is still separate. No frontend fixture engine participates
in real requests.

## Deployment

Supply server-provisioned opaque bearer credentials to `create_campaign_app`.
The same credentials authenticate the first WebSocket JSON message. Login,
credential issuance and refresh are outside this frozen slice. Provider credentials
stay in the server's provider runtime; they are never browser credentials.

For SQLite engine storage, the API creates a sibling `.v1.sqlite3` database for
persistent receipts, invitation claims, projection aliases and pagination. For a
Postgres engine, explicitly supply `v1_ledger_path`. All API workers serving a
campaign must share this same durable ledger on one host; independent replicas
with separate ledgers are unsupported. Back up both databases. `BEGIN IMMEDIATE`
serializes boundary reservations across processes; authoritative engine commits
still use the engine database transaction and its duplicate/CAS checks. Provider
calls run outside both transactions. Original command receipts are retained;
there is no automatic command-ID reuse or destructive retention job.

Configure an exact `v1_origins` allowlist for browser WebSockets. Missing Origin
is rejected unless `v1_allow_no_origin=True` explicitly permits native clients.
No wildcard Origin, URL credentials, compressed or binary application frames are
accepted. HTTPS/WSS is required except connections from loopback; terminate TLS
at the application or use a loopback reverse proxy. Do not blindly trust forwarded
identity/Origin headers. Connection admission is limited to eight connections per
IP and four per principal **per worker**. HTTP and WebSocket message budgets are
120/minute. WebSocket credentials expire with the connection after one hour;
removing a token from the application's credential mapping invalidates it.

The core adapter maps the engine's integer tick and weight units to 1,000 ms/tick
and one gram/unit by default. Deployments must use units consistent with their
trusted scenario/rule configuration. `V1Service` accepts explicit `tick_ms` and
`weight_grams` mappings. It does not recalculate character builds, rolls, inventory
costs or movement rules. Encumbrance is marked unavailable when the engine has
not supplied a derived encumbrance category, rather than guessed in transport.

## Commands and recovery

Every POST uses a principal-wide command-ID namespace, with method, concrete path
and canonical JSON in the fingerprint. Retries are reauthorized before returning
receipts. A different payload returns `idempotency_conflict`. Accepted actions
persist before background work, and exact engine attempts persist before dispatch.
The engine itself consumes resources and records the result atomically. Restart
recovery reuses that exact attempt after a crash between engine commit and receipt
finalization; it does not reroll or consume again. A response/provider failure
cannot turn a committed outcome into an engine rollback.

Visible resource versions are keyed hashes of permission-filtered content, not
campaign revision encodings. An additional authorization/version callback runs
inside the engine commit transaction. Hidden-only revision races may retry under
unchanged visible versions, with a three-attempt bound. Visible changes reject
with `stale_version`; clients refresh and reconsider instead of automatically
rewriting their intent. Pending interpretation can be cancelled; resolving and
terminal actions cannot. Clarifications retain the original action ID and have
their own command receipt and action-version check.

Configured providers receive only bounded v1 projections. Their typed intent or
clarification is validated before entering the normal engine path. Without a
provider, structured commands work and text capability is omitted. Narration is
separate ephemeral output over a committed result. Failure or interruption stops
prose while preserving the authoritative result.

A scene projection carries a required, non-empty `description`, but the authored
scene graph holds no prose for one: the projection repeats the location name
there. Clients treat a description identical to the title as absent and print
the name once rather than twice (#203). Giving scenes real authored prose is a
scene-graph and contract change, not a projection fix.

## Scoped live data

A snapshot pins one engine checkpoint and the API action records under the ledger
transaction. Every resource is projected before transmission, and each send
rechecks actor control, scene access and the visibility policy. Scope revocation
sends only `subscription.revoked`; a changed authorized view resets its epoch.
HTTP pagination also rechecks permissions and uses bound, expiring snapshots.
Unknown and inaccessible IDs share the same error shape. Unsupported query fields,
including invented search filters, are rejected rather than routed to raw history.

The engine's atomic `state_after` command log is the source outbox. The adapter
scans committed checkpoints after each scope's pinned boundary, projects only
states authorized at the time and now, and persists stable scope-local event IDs
and random cursor aliases before delivery. If it crashes before materialization,
the source log permits recovery. If a visibility change crosses the scan, the old
scope is reset instead of exposing historical data. Hidden-only changes do not
produce placeholder frames or advance the cursor. Membership grants do not grant
old player action history; explicitly authorized GMs may inspect actions recorded
during their grant.

Replay retains at most 20 minutes/10,000 projected events per scope. ACKs release
only output actually sent to that subscription. Unacknowledged output is bounded
by 256 frames/16 MiB; staged snapshots by 10,000 resources/32 MiB. Snapshot frames
are written serially with a send deadline. Heartbeat interval/deadline is 15/10
seconds. Replay excludes narration. A fresh subscription can request fresh
narration for a committed result, and interruption affects only its subscription.

## Capability and engine ownership

| Surface | Runtime status | Owner / later integration |
|---|---|---|
| Self, campaign/member/character/inventory/scene reads | Implemented, permission-filtered | #19, #50; perspective refinements #45 |
| Inspect, move, item use, wait | Adapted to existing typed engine; feasibility remains authoritative | #7/#8/#12/#18/#50 |
| Text and clarification | Enabled only with configured provider | #20/#27/#50 |
| Action receipts, cancellation, error/version mapping | Implemented | #50 |
| Invitations | Durable single-use claim; player/spectator grants without actor assignment or promotion | #40/#50 |
| Current session | 404 until an engine session record exists; no fabricated session or recap | #40 |
| Live snapshot/replay/revocation/narration | Implemented over the committed engine log | #45/#50; browser reconciliation #54 |
| Reviewed voice input / local narration | Browser adapters use the ordinary text action receipt; no server media route is advertised | #24/#57 |
| Character creation, advancement and scenario generation | Dedicated routes remain proposed | #21/#22/#37/#40 |
| Combat, equip/drop/store/transfer, split/capture/rescue, objectives/endings | Existing engines retain ownership; dedicated v1 command routes are not invented | #18/#38/#41/#43/#44/#45 and respective waves |

`actions.inspect`, `actions.move`, `actions.use_item`, `actions.wait` are the core
capabilities; `actions.text` is added only when the interpretation provider is
configured. An advertised capability must conform, so a kind the engine would
answer with `unsupported_action` is never claimed: `actions.inspect` appears only
where this viewpoint already sees a target carrying an authored inspection check,
and `actions.use_item` only where the scenario defines a consumable. Alongside
`actions.inspect`, the projection names each of those permitted observations as
`actions.inspect:<target_id>`, so the scoped entries never name a target the
caller cannot already see; they are omitted whole rather than truncated when the
list would exceed its bound, and a client that sees no scoped entry falls back to
the plain capability. Being
implemented by an engine does not automatically freeze a new HTTP intent variant.
A capability is not a substitute for per-command authorization or feasibility
checks.

## Validation

`tests/test_v1_api.py` starts real aiohttp services with two players and a GM. It
validates wire payloads against the frozen schemas and exercises concurrent exact
retry, restart recovery, critical-roll mapping, clarification, cancellation,
provider failure, invitations, hidden targets, bound pagination, scoped replay,
hidden-change stability and revocation. Runtime schema copies are included in the
wheel and compared against `contracts/v1` in CI; edit the source contracts only
through their reviewed versioning process. Existing Python and frontend checks
remain enabled. This delivers the core API slice, not the end-to-end acceptance
for every later engine feature in #59.
