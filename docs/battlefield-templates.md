# Battlefield template ownership

`CombatRules.battlefields` is the only owner of square and hex templates. Its
`coordinate_system` discriminator distinguishes `square-grid-v1` from
`hex-axial-v1`. The reader tags old untagged square configuration explicitly.
Standalone hex geometry values can omit their location for compatibility with
old migration requests, but configured templates must name an authored location.

Encounters store a discriminated spatial context. Square and hex contexts own the
template ID plus every exact actor position and facing; participant pose values
are runtime mirrors and are omitted from encounter serialization. Basic contexts
instead own bounded spatial facts and require no template. Ground-item state and
combat timing remain common encounter state. Geometry helpers take the selected
rules template explicitly. An unknown ID or a coordinate-system mismatch fails
closed. Both mapped kinds use the existing scene/location validation.

Basic contexts are executable through `StartBasicEncounter`, `BasicMove` and
`DeclareBasicSpatialFacts`; their provenance and invalidation rules are documented
in [Basic (mapless) combat](mapless-combat.md).

The existing GM `MigrateEncounterHex` command now installs its map under a
content-derived ID and appends a `MigrationEntry` with the old and new runtime
digests. It preserves the original square template, so other encounters and
historical references remain valid. The request must name the current map and
cannot relocate the encounter or change its posture.

For a retained campaign containing embedded maps, call
`orchestration.battlefield_templates.migrate_embedded_maps` with the GM ID,
command ID and expected revision. This uses the normal compare-and-set, command
receipt and event stream. It lifts every embedded map, preserves exact geometry
and actor poses, updates the configuration pin, and records the migration. It
never constructs geometry for a mapless encounter. Repeated requests return the
original result; the command can be re-executed without providers or fresh entropy.
Retained historical events still fold without rewriting their payloads.

Scenario-backed campaigns update their runtime graph and digest while retaining
immutable published-source provenance. Older typed campaigns without scenario
graphs persist the migrated CombatRules fragment in `combat_rules_json`. Their
registered profile continues to supply combat mechanics and equipment.

Tactical v1 keeps its frozen request schema via an adapter. Tactical v2 exposes
the location and darkness fields on migration input; regenerated clients and
scenario contracts describe the tagged configuration union. The encounter schema
contains no `HexBattlefield` definition.

Engine version 4 reflects the new encounter representation. Replay fixture inputs,
seeds and timestamps remain unchanged. The hex fixture's initial checkpoint was
lifted to the new template owner at its existing migration boundary; all five
fixtures were regenerated and re-executed against the pinned configuration.
