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
replay with event folding (#413/#418/#419) or capture wall-clock inputs (#414).

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
