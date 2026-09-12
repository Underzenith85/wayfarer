# Reusable scenario catalog

The **Scenarios** tab, beside the game modes, holds the **Scenario catalog** after token authentication; it is deliberately outside the numbered setup steps, which ask only which adventure to play (#261). Creating a game from a revision hands the shell back to setup, on the party screen the new draft opens at. Choose a bundled
scenario document or enter authored JSON, save a draft, reopen any saved revision, inspect its
diagnostics, publish it, and create a game from that revision. Assign legal characters to joined
players, mark ready, and start through the existing setup activation flow. Drafts exist independently
of campaigns. Incomplete and structurally invalid text can be saved and exported exactly.

The initial authoring interface is a JSON editor. Its source is the same
[scenario document v1](scenario-documents.md) used for authored and generated content. The older
setup graph editor remains available for existing direct-graph setup flows. Guided generation into
the catalog is the separate #84 integration.

## Permissions and identity

The runtime's configured token principals are allowed to author their own private scenarios,
matching the existing permission to host a game. `ScenarioCatalog` accepts an explicit server-side
author allowlist for other embeddings. Catalog reads, exports, writes, duplication and creation of
games additionally require ownership. Another principal gets 404, including for published content.
Document provenance and metadata never grant permissions. There is no public catalog sharing in this
version. Player-facing briefs are an explicit allowlist and omit graphs, notes and diagnostics.

Create, import and duplicate always create a new server-assigned catalog identity. Structurally valid
sources receive that scenario/graph ID and a new revision ID and ordinal; all remaining author content,
including graph-local references and provenance, is retained. Import never overwrites an existing
scenario, even when its input IDs collide. Reimporting with a new command creates a separate fork;
retrying the same command returns its existing receipt. Malformed, unknown-version and incompatible
imports fail. Valid but mechanically unfinished compatible documents can be imported as invalid drafts.
Save accepts invalid source text for later repair. Saving valid content canonicalizes it and assigns
revision identity; export returns the exact stored source. Copying a published revision does not copy
its publication status or its owner authority.

## Additive authoring API

This API uses `/authoring/v1/scenarios`; it does not alter the frozen `/api/v1` play contract.
All requests require `Authorization: Bearer <token>`. Writes require JSON. Python request and response
models live in `engine/simulation/catalog.py`; their generated JSON Schema is in
`contracts/authoring/v1/schemas.json`. Errors follow the existing HTTP error envelope: 400 invalid
input/activation, 401 missing credentials, 403 missing author authority, 404 inaccessible resource,
409 stale version or reused command identity, 429 rate limit.

| Method and suffix | Request | Response |
| --- | --- | --- |
| GET `/authoring/v1/scenarios` | — | Array of `CatalogSummary`, private to the owner |
| GET `/templates` | — | Bundled v1 `ScenarioDocument` array |
| POST collection | `CatalogCommand`: create or import, `content_json`, expected_version 0 | `CatalogSummary` |
| GET `/{id}?revision=N` | Optional saved ordinal; defaults to latest | `RevisionView` with source, saved and current diagnostics |
| POST `/{id}` | `CatalogCommand`: save, validate, publish, duplicate or archive | `CatalogSummary` |
| GET `/{id}/export?revision=N` | Optional saved ordinal | Raw stored author JSON/text |
| GET `/{id}/preview?revision=N` | Published ordinal | Filtered `PlayerScenarioExport` |
| POST `/{id}/instantiate` | `InstantiateRevision`: command `id`, revision ordinal, optional legal `party` | Existing setup/lobby response (201) |

Every catalog command carries a unique `id`. Saves, validation, publication and archive require the
current `expected_version`, distinct from the document revision ordinal. Duplicate requires an explicit
source `revision` and expected_version 0. Exact retries return the original catalog receipt even after
subsequent edits; reusing a command ID with different input/resource conflicts. Refresh after a stale
save rather than changing the precondition of the same command. Validate creates a new immutable draft
revision with fresh content/rules/party diagnostics; read also returns a fresh report without writing.
Publish revalidates the selected saved revision and attaches a canonical immutable published snapshot.
The optional party must fill every scenario slot and satisfy the server's compiler and power policy.

The client retains a failed request in memory for an exact retry. Reloading the catalog recovers saved
content from the server; browser storage and provider conversations are never the source of truth.
Document source is limited to 2 MB of UTF-8; the streaming HTTP envelope allows up to 12.1 MB to account
for JSON escaping and rejects oversized/chunked inputs. Existing authenticated rate limits apply.

## Persistence, backups and activation

`scenario_catalog` and `scenario_receipts` are separate tables in the configured SQLite or PostgreSQL
database. Catalog aggregates contain the owner, version, archive state and immutable draft history,
with publication snapshots and content/rules validation identities. Commands serialize transactionally
(SQLite immediate transactions; PostgreSQL transaction advisory lock), including first creation and
receipt insertion. No catalog entries appear as campaigns. The initial implementation serializes all
catalog writes and stores revision history in each aggregate; large catalogs may warrant normalized
revision rows and per-entry locking later.

Back up and restore both tables together with the campaign database using a consistent database backup.
JSON export/import preserves graph meaning but intentionally creates new identities and does not transfer
ACLs, receipts or publication authority. Restore the whole database to retain those identities and
history. Do not restore only one of the catalog and receipt tables.

Creating a game from a published revision binds the selected legal party and pins the full canonical
scenario document in `scenario_document_json` alongside the independent setup graph. The document
contains its source scenario/revision IDs. A deterministic campaign ID and durable creation payload make
retries safe across server restarts. The pinned setup cannot be edited in place; create another revision
and game to change it. Readiness and activation use the existing #40 studio/compiler path, with an
additional revalidation of the pinned document and actual party at activation. Active campaigns use their
saved runtime graph and remain resumable after catalog edits, archival or configuration changes.
Archival makes authoring read-only; published historical revisions remain exportable and reusable.
