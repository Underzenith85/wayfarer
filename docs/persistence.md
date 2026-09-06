# Command, event, snapshot, and replay contract

Each committed command records a campaign ID, command ID, actor ID, expected and
resulting revisions, canonical payload hash, rules version, event schema version,
mechanical event, and complete resulting projection. The event and projection are
written in one transaction. Dice results are part of the mechanical event and are
therefore replayed rather than rerolled.

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
