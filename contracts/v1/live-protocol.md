# Scoped live protocol 1.0.0

Issue #48. `events.schema.json` is the reviewed target contract for JSON messages;
`events.examples.json` covers every message variant and `events.scenarios.json`
contains executable consumer recovery traces. These share the frozen HTTP payloads
in `schemas.json`. Existing HTTP schemas and operation semantics are unchanged.
Runtime routing, authentication, authorization, persistence and browser integration
belong to #50/#45/#54; this PR is not a WebSocket server implementation.

## Connect and authenticate

Connect to `wss://<service>/api/v1/live` with WebSocket subprotocol
`wayfarer.live.v1`. The server must select that exact subprotocol or reject the
upgrade. Use `ws` only for loopback development. Follow [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455.html).
Each application message is one UTF-8 JSON text message; fragmentation must be
reassembled within the byte limit. Binary application messages and compression
extensions are not negotiated in v1. No bearer token in URL/query/subprotocol.

Browser WebSocket clients authenticate through the first JSON message,
`authenticate`, containing the same opaque credential as HTTP bearer auth.
Authenticate within 5 seconds. Only that message is legal before authentication;
no campaign existence, subscription, heartbeat metadata or data is sent beforehand.
On success, `authenticated` echoes request_id and supplies connection_id,
principal_id and the enforced credential/connection expires_at (the earlier of credential expiry or
one hour after authentication). Reauthentication
on an established socket is invalid; reconnect with a new connection on expiry.
Authenticate frames and credentials must be redacted from logs and telemetry.

Check an exact configured Origin allowlist during upgrade; no wildcard origins.
Non-browser clients without Origin require an explicit deployment policy and the
same credential validation. Origin is not authentication. Apply connection/IP
limits before upgrade and principal limits after authentication. At most four
active subscriptions per connection; connection caps per principal/IP are deployment
configuration and must be documented. Limit client messages to 120/minute per
connection, including acknowledgements and heartbeats. On rate limiting, report
`rate_limited` with retry_after_ms and close 4429; do not keep an indefinitely
backlogged connection alive.

`request_id` is a client-generated UUID unique per control request on a connection.
It correlates replies only, never authorizes or executes gameplay. Duplicate IDs
with different control content are invalid_message. Exact retries of subscribe,
unsubscribe or interruption return the same control outcome while the connection
is alive (bounded by the request rate and connection lifetime); no gameplay is
repeated. Authentication is accepted once. ACK and pong are naturally idempotent.
Gameplay commands and clarifications stay on the #47 HTTP endpoints with durable
command IDs. Socket failure never means an accepted HTTP action was cancelled.

## Scope and permissions

A subscription scope is exactly `(campaign_id, scene_id, actor_id)`, where actor_id
is the **viewpoint**, not the speaker or initiating actor. The principal comes only
from authenticated state. Subscribe is allowed for the controlling player or an
explicit GM viewing that actor's perspective. This v1 socket does not grant a
spectator an arbitrary viewpoint; spectator live views require a separately reviewed
explicit grant contract. HTTP GM inspection remains available; there is no implicit
omniscient LLM subscription. Scene access and actor control are rechecked throughout.

The client supplies a fresh subscription_id UUID. Never reuse it for a different
scope or a new subscription lifetime, including after reset/unsubscribe. An unknown
or forbidden scope returns identical error `not_found` without echoing its hidden
contents. A successful `subscribed` echoes the request and exact scope, assigns an
opaque visibility_epoch, and selects snapshot or replay. Never trust client epoch
as proof of access. Multiple scopes on one connection are independently ordered.

Project every field before enqueueing and recheck authorization immediately before
serialization/delivery, including event metadata, IDs, timestamps, resource versions,
correlation/causation links, narration and voice. Historical data must satisfy both
permission at the event's time and current permission. Newly granted access does
not make old secrets retrospectively visible. Never serialize raw global events
and merely blank out outcome text.

A visibility_epoch is a random token tied to the current principal's permitted
view of that scope. Rotate on relevant membership, controller, perspective or
knowledge-policy changes, including reunion; no rotation for unrelated hidden
activity. Epoch changes invalidate resume credentials and any in-flight snapshot.
If scope remains permitted, send `stream.reset: visibility_changed`; if access is
lost, send `subscription.revoked` with only the already-known subscription_id.
These controls are a serialization barrier: remove old queued frames first, stop
narration/audio, then send the control, then terminate that subscription. Never
send old-scope frames after it. Reconnecting revalidates before any replay.

## Durable updates and ordering

`action.updated` and `projection.invalidated` are the only durable event variants
in this version. They describe persisted, authorized state. Each includes:

| Field | Meaning |
|---|---|
| protocol_version | Exact 1.0.0 contract; negotiate another subprotocol for incompatible versions |
| subscription_id, scope, visibility_epoch | Delivery context; never shared between perspectives |
| event_id | Random stable ID for this projected event, unchanged on permitted replay |
| previous_cursor, cursor | Opaque chain positions before/after the event |
| occurred_at | UTC time of this permitted projected event; no hidden global clock metadata |
| correlation_id | Authorized operation/turn UUID, independent of provider credentials |
| causation_id | Permitted preceding command/event UUID, or null when absent or hidden |

Cursors are unguessable authenticated tokens or random server-side handles bound to
principal, exact scope, epoch, protocol version and position. No encoded plaintext
global revision, sequence, actor ID, hidden count or other perspective's position.
Event IDs and correlation tokens are scoped aliases where a global ID would reveal
private activity. `previous_cursor == last_applied_cursor` is the only ordering
comparison; never lexically/numerically compare opaque cursors or resource versions.
No empty placeholder events or cursor advances are emitted for hidden activity.
There is no global ordering guarantee across subscriptions or scenes. Shared-time
causality is an engine responsibility (#45), not something a browser infers by
sorting wall-clock timestamps.

Delivery is **at least once within retained authorized history**, not exactly once.
A durable event and its projection/outbox record must become recoverable atomically
with the originating commit. Processing workers cannot publish two different
payloads with the same projected event ID. Apply each event once: duplicates with
identical ID/cursors/payload are ignored, including redelivery after reconnect.
The transport envelope may have a new subscription_id; the projected event content
is unchanged. A repeated ID with changed content is a protocol error and forces
reset. Keep a bounded dedupe ledger for the current replay window; if an old
unknown duplicate cannot be proven redundant, fail closed to resync.

A previous_cursor mismatch means a gap/out-of-order delivery. WebSockets preserve
wire order, but asynchronous client callbacks can still process messages wrongly;
serialize processing. Do not reorder by timestamp or apply later events while
waiting. Stop that subscription and resume from the last applied cursor, or take
a fresh snapshot if the corresponding cache is unavailable. The trace checker
models this conservative resync behavior. ACK only the highest contiguous applied
cursor, after cache changes are persisted atomically with it. Batch ACKs at most
once per second (and on idle boundaries); ACK is flow control, never a game commit.
ACKs must identify a cursor already sent to that authenticated subscription.

`action.updated` contains the existing Action union, including terminal resolution
and its changed_resources. Before advancing the cursor, invalidate each changed
resource in the query cache. `projection.invalidated` marks its listed resources
dirty before cursor advancement. Both use the HTTP ResourceVersion type. Fetch
fresh authorized HTTP projections to clear dirty entries; opaque versions cannot
be compared for greater-than. Tag each fetch with the local epoch and invalidation
generation; discard responses when either changed while it was in flight. Later
invalidation wins over any older HTTP request. Until refreshed, show last synced
state as stale and submit commands only with fresh required versions.

This is not an atomic multi-resource UI patch protocol. A resolution is authoritative,
but its resource views can temporarily be loading. Snapshots provide atomic recovery.
Preserve dirty flags alongside the resume cursor; an absent or inconsistent cache
requires snapshot, even if an old cursor is available. HTTP action reads remain the
recovery path for pending/terminal operations, including outcomes whose socket
messages were missed.

## Race-free snapshot and replay

`subscribe.resume` is null for a new view or contains cursor + visibility_epoch
when the client still has the corresponding valid persisted cache and dirty flags.
Do not assemble a baseline using unrelated paginated HTTP reads; they are not one
transaction and cannot establish a race-free stream cursor.

For a fresh snapshot the server must:

1. Authorize the scope and pin the epoch. In one consistent read/outbox boundary,
   register the subscription tail and capture an MVCC snapshot plus cursor C.
2. Send subscribed(mode=snapshot), then snapshot.begin(snapshot_id, C, count).
   Buffer only authorized durable events strictly after C while serializing.
3. Send indexed snapshot.resource frames 0 through count-1 from that same snapshot.
   Each resource is a typed campaign, scene, character, inventory or action record.
   Exactly one of campaign/scene/character/inventory is required for this scope;
   include all pending owned actions in this scene and up to 100 most recent terminal
   actions. Remaining history is available through HTTP. No duplicate resource keys.
4. Revalidate epoch/access, then send snapshot.end with identical snapshot_id,
   C and count. Client atomically replaces the scoped cache and cursor only now.
   Partial snapshots never replace or augment the visible cache.
5. Drain buffered post-C events in cursor order and send stream.ready at the last
   drained cursor. Live events may follow immediately. The client has reached the
   handoff boundary only on ready; it does not need to wait for permanent idleness.

A transaction committing between steps 1 and 5 is either included in the snapshot
or in the post-C tail exactly once logically (duplicates remain harmless).
Implementation may use an MVCC/outbox equivalent, but a read-then-listen sequence
with a gap is nonconforming. Resource array limits and byte limits apply; if the
complete required snapshot cannot fit, return snapshot_too_large and close the
subscription. Never truncate mandatory pending state and claim a complete snapshot.

For replay, authorize before decoding/loading the cursor. Retain up to 20 minutes
and 10,000 events per authorized scope/epoch, whichever limit is reached first;
these are maximum bounds, not a promise that replay is always available. Pin the
replay-to-live boundary atomically, send subscribed(mode=replay), replay events
strictly after the supplied cursor, then ready at the captured tail. If already at
tail, ready echoes the resume cursor. Do not send an older snapshot over a valid
replay cache. Durable replay must not include ephemeral narration/audio.

Expired/evicted cursor: reset(cursor_expired). Valid cursor from a changed epoch:
reset(visibility_changed). History unavailable after service recovery:
reset(resume_unavailable). Wrong-principal/scope/tampered cursor: generic
error(invalid_cursor), never accept as a fresh subscription. After a reset, purge
the old scoped cache, cursor and staged state, then subscribe with a **new ID**
and resume=null, respecting retry_after_ms. On authentication/access failure, purge
and wait for access recovery; do not retry with another actor's credentials.

## Narration and voice

Narration is ephemeral and is **not replayed**. `narration.started` identifies a
scope-private narration_id, authorized action_id/command_id, provisional/committed
basis and nullable voice_session_id. IDs do not reveal another perspective's
narration. Provisional prose is visibly provisional and cannot change HP, inventory,
objectives, cursor or committed action status. Committed basis requires an already
succeeded Action in this authorized view; the result survives provider failure.

`narration.delta` indexes text chunks starting at zero. Ignore an identical duplicate;
a gap or changed duplicate stops only that narration and discards its partial output.
Do not replay speech when catching up durable state. `narration.ended` specifies
completed/interrupted/failed and the number of emitted chunks. Connection loss,
reset, revoke and scene exit discard partial narration/audio buffers and stop
playback. Neither reset nor narration failure changes an Action's terminal outcome.

`narration.interrupt` requests interruption of one narration on the caller's
subscription, not engine action cancellation. On an active permitted narration,
stop future chunks and send ended(interrupted) with the control request_id. If it
already ended, return that ended state with the request_id; no resurrection. Unknown
or foreign narration returns not_found without acknowledging its existence. Once
this boundary is acknowledged, no more deltas/voice segments may be emitted for it.
HTTP cancellation rules remain #47, including rejection after resolving/commit.

`voice.segment` is timing/routing metadata only: narration_id, voice_session_id,
segment_index, duration_ms, media_type and sample_rate_hz. It carries no audio bytes,
transcripts, credential, URL or provider identifier. It must match an active
narration's non-null voice session in this exact scope. Sequence/dedupe rules apply
per voice stream. Actual media negotiation/transport and microphone transcript
review remain #24/#57; do not advertise voice capability before that integration.
A player may hear only their authorized viewpoint stream. One player's interruption
must never stop another player's or scene's narration.

## Backpressure, reconnect and close behavior

The schema's x-limits freezes application defaults:

| Limit | Value |
|---|---|
| Client JSON message | 32,000 bytes after reassembly |
| Server JSON message | 8 MiB after reassembly, including envelope |
| Staged snapshot | 32 MiB, at most 10,000 resources |
| Pending server tail/replay output | 16 MiB or 256 frames, whichever comes first |
| Active subscriptions | 4 per connection |
| Auth timeout | 5 seconds |
| Heartbeat interval / response deadline | 15 seconds / 10 seconds |
| Replay maximum age / events | 20 minutes / 10,000 |

The large server-frame limit accommodates the HTTP inventory's bounded 1,000
stacks; it is a ceiling, not a target batch size. Check limits before allocation,
including fragmented message aggregation and buffered snapshots. If unacknowledged durable
output or the buffered post-snapshot tail exceeds either pending bound, remove pending private frames, send
reset(backpressure) if possible, then terminate the subscription; if unable to
send control, close the socket 1013 and reconnect from the last safely applied
cursor/cache. Never silently drop durable events or grow memory without limit.
Snapshot production must stream its pinned read rather than enqueue all resources;
count undrained snapshot writes toward transport bounds, then release their
transport buffer accounting when drained (snapshot ACK is not required per resource). Snapshots cannot rely on durable
ACK until end, so a persistently slow reader may be disconnected and must retry.

Server sends application heartbeat.ping with random nonce after authentication
at a fixed interval independent of hidden gameplay. Client returns pong with that
nonce within ten seconds. This is separate from browser-managed WebSocket ping/pong
frames. Missing pong closes 4408. Missing any traffic for 25 seconds lets the client
close/reconnect. Credential expiration/revocation closes 4401 and clears all scoped
caches/audio; resumable network failures keep only internally consistent cache and
cursor checkpoints. Expires_at also bounds control-request dedupe retention.

| Code | Meaning / client action |
|---|---|
| 1000 | Normal closure; stop listening |
| 1002 / 1003 / 1007 / 1009 | Protocol, binary, invalid UTF-8, oversized frame; fix request, do not loop |
| 1011 / 1013 | Server failure / overload; bounded retry |
| 4401 | Authentication failure/expiry; purge all connection scopes, obtain valid credentials |
| 4408 | Auth/heartbeat timeout; reconnect if credentials remain valid |
| 4429 | Rate limit; use error retry delay, otherwise exponential backoff |

On transient failure use jittered exponential backoff starting at 500 ms, capped at
30 seconds; respect larger advertised retry delay. Do not repeatedly retry terminal
permission/protocol errors. Retry count itself cannot reexecute a game command.
Reconnect begins with new authentication and fresh subscription IDs. Old connection
callbacks cannot mutate new connection caches; filter by local connection generation
before parsing scoped payloads. Unknown protocol versions terminate instead of
silently skipping events whose effects might be essential.

## Scene switch, capture and reunion

Selecting a new scene is not a gameplay move. Use the authoritative engine's scene
transfer/rejoin commands, then subscribe only after access is granted. Unsubscribe
old view, stop text/audio presentation, and purge its cached private projection.
The server responds unsubscribed after removing queued output; late callbacks from
that retired subscription are ignored. Capture can revoke/change views while another
group continues; it does not globally stall their decisions or terminate the campaign.
Reunion rotates affected epochs and rebuilds each viewpoint independently, without
copying captive secrets into the rescuer's snapshot. Independently authenticated
players receive distinct scopes/cursors even when sharing one scene.

## Validation and integration gates

```bash
uv run --frozen python -m scripts.validate_live_contracts
uv run --frozen pytest tests/test_live_contracts.py --no-cov
```

Schemas and standalone messages are checked offline with Draft 2020-12 and the
shared #47 payloads. Executable traces assert snapshot atomicity, cursor-chain
recovery, duplicate handling, narration interruption after commit, expired cursor,
revocation, scene switch/reunion and independent captive/rescuer views. Injected
bad/stale client-delivery frames are explicitly marked; they are not examples of
permitted server publication. The trace checker is a bounded consumer contract
oracle, not a production client or a proof of server authorization. #50/#45 must
add live server tests for access revocation during serialization, MVCC/outbox race,
retention, ACK limits and two distinct identities; #49/#54 consume these fixtures.

All message variants are closed unions. Incompatible changes require a new
subprotocol/schema version and coordinated review; documentary clarifications may
retain 1.0.0. No event contract silently widens the frozen HTTP Action union.
