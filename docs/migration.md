# Existing campaign compatibility and backups

This release preserves the original `campaigns(id, state)` and
`events(campaign, request_id, payload)` SQLite tables and the JSON state shape.
There is no schema conversion, point recalculation or rules-version upgrade.
The automated migration test loads the old schema directly and proves that old
request IDs remain idempotent while new turns can continue.

## Upgrade in place

1. Stop the old server before copying or replacing the application.
2. Back up `data/wayfarer.sqlite3` to a separate location.
3. Run `uv sync --frozen` in the new checkout.
4. Run `uv run --frozen wayfarer --db /absolute/path/to/wayfarer.sqlite3`.

The default remains `data/wayfarer.sqlite3` relative to the launch directory.
When launching from another directory, explicitly set `--db` or `WAYFARER_DB`
to the existing database. A missing path creates a new empty database; it does
not automatically locate or import an old campaign. Browser storage only
remembers the selected campaign ID and is not a backup.

## Export/import

For this unchanged schema, the supported export is a complete SQLite database
backup, retaining both current state and event/request history. With the server
stopped, copy the database file; to import, copy that backup to a new path and
launch using `--db` pointing to it. Do not overwrite a running database. For a
live backup, use SQLite's backup API instead of copying a potentially active file.
No JSON import API is provided, and merging campaigns from two databases is not
supported in this wave. Keep the original backup until the restored campaigns
have been verified.

Future catalog/schema migrations require a separate versioned migration workflow
(#18). Do not reinterpret existing demo data as official Fourth Edition builds.

## Rules profile migration

Saved campaigns keep their exact rules pins; #96 registers those pins as
`profile:wayfarer-lite@1` without changing stored data. Selecting another
registered, supported profile for a paused or completed game is an explicit host
action through the rules-migration ledger: preview incompatibilities, then apply
with a command receipt, expected revision and source digest. Failed migrations
write nothing, and retries of the same command are idempotent. See
[rules profiles](rules-profiles.md).
