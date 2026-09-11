# Command, event, snapshot, and replay contract

Each committed command records a campaign ID, command ID, actor ID, expected and
resulting revisions, canonical payload hash, rules version, event schema version,
mechanical event, and complete resulting projection. The event and projection are
written in one transaction. Dice results are part of the mechanical event and are
therefore replayed rather than rerolled.

## Command entropy (#411)

Live command services enter through `orchestration.entropy.commit_command`.
Before entering persistence it captures a fresh 256-bit seed. During the
synchronous resolver, all `CommandRandom` handles use one task-local source,
including the main reducer and checkpoint hooks. The scope is reset in `finally`;
shared engines retain no RNG state. Simulation receives only the `RandomSource`
protocol and rejects random resolution when a caller omitted its source.

The command log stores private `entropy_seed`, `engine_version` and
`rng_algorithm` columns in the same transaction as the event and resulting state.
These fields do not participate in payload hashes and are never copied to the
campaign, event payload, narration input, projection or live stream. Duplicate
requests return the winning receipt without executing the callback; any seed
allocated to a losing attempt is discarded. Failed transactions retain no seed.
System-authorized resource/clock commands use the `system` principal; ordinary
commands retain their authenticated actor or principal attribution. Checkpoint
effects within a command remain part of that command's receipt and RNG stream.

`sha256-counter-v1` is a fixed SHA-256 counter stream with rejection sampling for
arbitrary positive bounds, not Python's version-dependent random implementation.
Its byte-level contract is frozen by a test vector. `simulation.ENGINE_VERSION`
started at `1`; the event-list contract uses `2`. Bump it when any input can produce different state, events or draws,
including RNG algorithm changes. #418 enforces the fixture version/replay gate described below.

`CommandRecord.reexecutable` requires a seed, a supported RNG algorithm and the
running engine version. Additive SQLite/PostgreSQL migrations leave these fields
null on old rows: no seed is fabricated. Existing explicit scripted RNG injection
is retained for numeric fixtures, with `rng_algorithm="injected"`; those records
are deliberately not claimed to support seed-only re-execution. Production
services use the seeded source by default. This step does not replace snapshot
replay with event folding (#413/#418/#419).

## Command time (#414)

`commit_command` captures one `CommandInstant` before entering persistence, or
accepts an explicitly supplied instant for recovery and replay. The command log
stores its UTC Unix microseconds in `recorded_at_us` alongside the event and
resulting state. Retries retain the winning command's timestamp. This private
metadata does not change payload hashes or public event and campaign schemas.
SQLite and PostgreSQL migrations leave historical timestamps null rather than
inventing an original execution time.

Invitation handling captures time at entry. Creation computes the deadline from
that value; redemption checks the deadline against the supplied instant, accepting
the exact deadline. The saved claim receipt carries the instant into the domain
command and completion receipt. A restart after saving a claim reuses its original
instant even after expiry. Legacy claims without a recorded instant retain their
existing retry authorization, but their original acceptance time is unknown.

Ledger transactions capture an instant before acquiring the write lock, or accept
one from the caller. Action timestamps, projection metadata and outbox timestamps
and retention checks consume that value throughout the transaction. Provider
telemetry already has no wall-clock reads. Operational provider timeouts, HTTP rate
limits, cursor lifetimes and socket heartbeats remain outside simulation. An
architecture gate rejects clock imports in simulation and clock reads in command
resolver callbacks and their local helpers. The command re-execution gate is described below.

## Command origins (#412)

Model-originated commands carry an optional private `origin_json` annotation with
schema version, validated proposal type and JSON, provider/model identifiers, and
a SHA-256 digest of the canonical proposal. Only structured, validated proposals
are retained; credentials, account details, provider envelopes and raw output are
excluded. Custom providers identify themselves as `custom` with an unknown model
unless they supply these bounded fields. Responses and Codex adapters supply their
configured model and provider identifiers.

Origins do not participate in command digests, engine inputs, campaign state,
projections or narration. Exact retries preserve the winning annotation. Direct
client commands have no origin. Orchestration forwards annotations through a
scoped task-local boundary, reset even on failure. Director interpretation receipts
and private v1 action records retain proposals before dispatch so recovery can
attach the same origin without requesting another interpretation. Trusted NPC
proposal callers use the same origin shape; authored NPC decisions have none.
Draft-only provider operations do not commit commands and therefore create no
command-origin rows. Replay consumes stored commands/state without calling a
provider. SQLite/PostgreSQL migrations leave legacy origins null.

## Dedicated event stream (#413)

`CommandRecord` describes a receipt; `StoredEvent` describes one ordered row in
`event_stream` (campaign, revision, ordinal, command ID, schema version, event).
`ActionEngine.resolve` returns `(state, list[EngineEvent])`; callers extract the
`ActionResolved` result for existing response contracts. The command boundary
composes each service reducer with its checkpoints and emits the final event list
before storage. Mechanical helpers retain their focused result types internally.

The list includes typed action, scene, resource, combat, spell, ability, injury,
fright and hazard facts. Other encoded procedure records remain resource facts
for this migration. A private `StatePatched` event records explicit path changes, with base
and result digests, covering state that is not yet represented by a specialized
fact. It does not contain a replacement campaign snapshot. Folding applies those
changes; semantic facts can be consumed independently without applying effects a
second time. Embedded event fields remain until #419.

Each event declares a campaign, actor-set or GM audience. Scene discoveries and
action results are private to their observer/actor. Full mechanical traces and
state patches are GM-only because they can contain hidden target facts. A
payload-free projection refresh hint asks consumers to check their authorized
view; unchanged views emit no wire message. The v1 projector and outbox reconstruct
from the event stream and filter audiences before projecting. Opaque cursor and
version behavior remains unchanged; internal patches never cross the live wire.

Adapters append the command, all event rows and snapshot in one transaction under
the existing compare-and-set. Before append, folding must reproduce the candidate
state. Failure rolls back everything; retries do not append again. Ordinals order
multiple legacy fixture writes at the same revision. Live engine commands still
advance the revision. The architecture gate permits event-stream inserts only in
the two persistence adapters and forbids update/delete statements.

`stream_genesis` retains the earliest available checkpoint. Existing logs are
converted once under the writer lock using their recorded transitions, without
fabricating seeds. If an old database retains no revision-zero checkpoint, its
earliest snapshot is the explicit reconstruction boundary. Pre-event-store
databases without any snapshots use their current campaign row as that boundary. Subsequent stream
reads no longer depend on command `state_after` columns or the snapshots table.
This step retains the existing snapshot-based general load path; #419 demotes
that cache after the replay gate and upcaster registry land.

`contracts/v1/events.schema.json` links to the separately versioned
`engine-events.schema.json`, defining the engine event union and schema-version-1
`StoredEngineEvent`. The frozen live schema has no semantic changes. The offline validator
checks the definitions against the runtime models; schema changes require review.
Run `uv run python -m scripts.update_engine_event_schema` to regenerate the
engine definitions while preserving the wire definitions.

## Fold and re-execution gate (#418)

`persistence.replay.verify_commands` checks two independent guarantees. Folding
applies the event stream and compares each result with the command's stored
snapshot, regardless of `ENGINE_VERSION`. Re-execution dispatches the saved typed
command through an isolated engine using its original seed and recorded instant,
and compares the resulting state, event list and embedded dice. Neither providers
nor origin annotations are execution inputs. The rules configuration digest must
match the caller's trusted pin before and after every command; a rules-data
migration remains a separate `MigrationEntry` concern.

Commands now retain their exact `command_input` alongside the receipt. Old rows
remain null; a transcript input is recoverable only if its original payload digest
matches. Missing seeds, missing recorded time, unsupported RNGs and different
engine versions produce explicit `ReplayCheck` reasons while the fold still runs.
Malformed current command inputs fail validation. Unknown command families fail
explicitly rather than invoking interpretation or trusting a stored outcome.
The initial replay dispatcher covers typed actions, scenes, parties, combat,
spells and authored recovery; callers can supply another typed executor to the
verification API. Re-execution is a fixture guarantee, not a claim that every
historical command family can run under current code.

Five reviewed goldens under `tests/fixtures/replay/` cover the reference adventure,
capture/rescue, hex combat, a spell and recovery. They retain their initial play
checkpoint, exact command inputs, seeds, instants, typed events and independent
snapshot digests. Fixtures begin at their explicit configured play checkpoint;
setup/genesis migration remains #422. `test_release_invariants.py` verifies both
checks at every fixture revision, with provider calls and fresh entropy/time
capture forbidden. `scripts/release_gates.py` requires all five cases plus the
SQLite/PostgreSQL durable replay and corruption tests.

To review a deliberate engine behavior change:

1. Bump `simulation.ENGINE_VERSION` in the same change as the behavior.
2. Run `uv run python -m scripts.regenerate_replay_fixtures`.
3. Review the event/dice diffs and snapshot digests, then run
   `uv run python -m scripts.regenerate_replay_fixtures --check` and the release tests.

Regeneration preserves inputs, seeds, time and initial state. It refuses to bless
changed output under an unchanged engine version. The gate rejects a version bump
without regenerated fixtures as well. Rules-data changes cannot be silently
accepted by rewriting the pinned configuration digest.

## Receipts and recovery

Command IDs are unique per campaign. An exact retry returns the original result,
even if later commands have committed. Reusing the ID with a different payload is
a conflict. PostgreSQL locks the campaign row and SQLite uses `BEGIN IMMEDIATE`;
both recheck the revision while holding the write lock.

Revision-zero and periodic snapshots bound recovery work. Replay starts at the
latest applicable snapshot and applies immutable `state_after` projections in
revision order. Event schema versions allow future upcasters without rewriting
history. Model calls and narration remain outside write transactions.

Set `WAYFARER_DATABASE_URL` to a PostgreSQL connection URL for the production
adapter. Without it, Wayfarer retains the local SQLite adapter and migrates legacy
databases in place by adding the new log and snapshot tables.

Reusable scenarios use separate catalog and command-receipt tables in the same configured database.
See [scenario catalog storage, export and restore](scenario-catalog.md) for authoring semantics and
consistent-backup requirements.


### Retained schema readers (#427)

Both adapters pass raw event JSON through `persistence/upcasters.py` before folding.
The registry is keyed by event kind and the row's schema version; it rejects future
versions and missing intermediate migrations. Each pure migration takes and returns
a JSON mapping, operates on a defensive copy, and belongs beside the event definition
it migrates. Defaulted additions need no migration. Writers use each kind's current
version; stream consumers receive its normalized current version. Command receipts
use the same registry mechanism before being returned by `history`.

Version 1 is the first persisted event and command shape; no fictional historical
shape is introduced. `tests/fixtures/retained_schemas.json` freezes the retained
versions and a real fold checkpoint. Release evidence lists those versions and
rejects any missing reader. Structural migration tests exercise ordered rename and
restructure steps and both adapters through the shared registry.

Before retiring a reader, collect `await store.schema_usage()` from every deployed
store and supply the combined JSON to `scripts/release_gates.py --schema-usage`.
The inventory reports each campaign/kind/version's last event revision and latest
snapshot. Retirement is blocked while any campaign lacks a snapshot at or past that
last revision. Coverage is necessary, not permission to discard historical replay:
retained fixture promises must also be explicitly reviewed before removal. Schema
migration happens on read; stored stream rows are never rewritten.
