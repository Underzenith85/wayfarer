# Backups and database moves

Wayfarer has not launched, so there is no compatibility contract with an older
database: each adapter declares its schema once and reads exactly what this
release writes (#634). Point a new checkout at a database this release created,
or start a fresh one.

## Moving a database

1. Stop the old server before copying or replacing the application.
2. Back up `data/wayfarer.sqlite3` to a separate location.
3. Run `uv sync --frozen` in the new checkout.
4. Run `uv run --frozen wayfarer --db /absolute/path/to/wayfarer.sqlite3`.

The default remains `data/wayfarer.sqlite3` relative to the launch directory.
When launching from another directory, explicitly set `--db` or `WAYFARER_DB`
to the existing database. A missing path creates a new empty database; it does
not automatically locate an older one. Browser storage only remembers the
selected campaign ID and is not a backup.

## Export/import

The supported export is a complete SQLite database backup, retaining current
state and the command log and event stream behind it. With the server stopped,
copy the database file; to import, copy that backup to a new path and launch
using `--db` pointing to it. Do not overwrite a running database. For a live
backup, use SQLite's backup API instead of copying a potentially active file.
No JSON import API is provided, and merging campaigns from two databases is not
supported. Keep the original backup until the restored campaigns have been
verified.

Stored event rows are upcast on read through the registry in
[persistence](persistence.md#retained-schema-readers-427); nothing else migrates
a database in place.

## Rules profile migration

Saved campaigns keep their exact rules pins; #96 registers those pins as
`profile:wayfarer-lite@1` without changing stored data. Selecting another
registered, supported profile for a paused or completed game is an explicit host
action through the rules-migration ledger: preview incompatibilities, then apply
with a command receipt, expected revision and source digest. Failed migrations
write nothing, and retries of the same command are idempotent. See
[rules profiles](rules-profiles.md).
