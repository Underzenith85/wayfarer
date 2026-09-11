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
starts at `1`; bump it when any input can produce different state, events or draws,
including RNG algorithm changes. #418 adds the comprehensive version/replay gate.

`StoredEvent.reexecutable` requires a seed, a supported RNG algorithm and the
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
resolver callbacks and their local helpers. The comprehensive command re-execution
gate remains part of #418.

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
