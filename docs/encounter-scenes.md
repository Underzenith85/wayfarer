# Encounter scene ownership

Issue #322 adds scene bindings and scheduling invariants, without changing GURPS
mechanics, spatial representations or the shared-time algorithm. Basic/mapless
combat is implemented by #324; the legacy square grid is not basic combat.

A scene-aware encounter stores `version: 2` and `scene_id`. For active encounters,
all participants must have that actor scene, their world location must match the
scene (and the battlefield for mapped encounters), and all must share one scheduling subgroup. Each actor and
each subgroup can belong to at most one active combat encounter. Independent
groups can each own a fight, including in the same scene. Completed encounters
retain their historical scene after actors travel.

`simulation.encounter_context.activity_for` derives the actor's scene, subgroup,
active encounter, subgroup encounter, queued activity, pending noncombat choices
and square/hex representation. It is internal authoritative context, not a safe
player projection. A spectator can have a subgroup encounter without personally
participating. Scheduling membership neither grants knowledge nor determines
allegiance.

## Splitting and setbacks

A nonparticipating member may explicitly `split_party` from a fighting subgroup
at the existing synchronization barrier. Active combatants cannot use this to
withdraw. Pending defenses/unarmed decisions, Wait interruptions, blocked rulings,
queued work, cross-scene effects, paused source groups and outstanding clock
commitments still block the split. The actor stays in the same scene, the source
generation increments, and no resources, knowledge or combat state are copied.
The new group can then queue independent work; world time remains the minimum
ready-through frontier. Rejoining a fight's group waits for resolved interactions.

Authored capture/incapacitation setbacks which separate a combatant into a recovery
group complete the old encounter with its setback reason before separating the
clocks. This repairs the prior cross-subgroup active-encounter state. Capture,
confiscation and rescue still execute through existing recovery rules. The fight
cannot continue advancing one group's clock on behalf of both groups. Individual
withdrawal while the remaining fight continues is separately tracked in #330.

## Save compatibility and migration

Missing encounter version/scene fields mean legacy version 1. Those defaults are
omitted when serialized so unmodified legacy checkpoints retain their shape.
For configured scenes, reads normalize a legacy encounter only when its battlefield
location maps to exactly one authored scene. This changes no revision, time,
profile/configuration pin, knowledge or stored history. The next normal commit
persists the structural normalization. Replay of historical checkpoints remains
byte-preserving through the existing store.

Ambiguous legacy encounters remain readable. Combat commands require an explicit
binding before proceeding; current actor whereabouts are not used to invent a
historical scene. A GM can submit this additive command to the existing
`POST /campaigns/{cid}/commands` adapter (not the frozen `/api/v1` contract):

```json
{
  "kind": "migrate_encounter_scenes",
  "id": "bind-legacy-fight",
  "actor_id": "gm",
  "expected_revision": 12,
  "bindings": [{"encounter_id": "fight", "scene_id": "warehouse"}]
}
```

Bindings must identify existing encounters and configured scenes at the battlefield
location. Existing scene bindings cannot be reassigned. Active participant cursors
must match. The command uses GM authority, CAS and durable receipts and commits
only metadata/revision: no checkpoint effects, dice, discovery or clock advance.
`StartEncounter` also accepts optional `scene_id`; it is required when a new fight's
battlefield location has multiple authored scenes.

Scene-less profiles retain version-1 behavior; no synthetic scenes or implicit rule
migration are introduced. To adopt scene rules, use the existing explicitly approved
`MigrationService(current, target)` with the target scene configuration. Its
`ApplyMigration` now accepts `actor_scenes` (one explicit cursor per actor, preserving
world locations) and `encounter_scenes` (explicit bindings for ambiguous encounters).
Missing actor mappings or ambiguous encounter mappings reject atomically. Existing
scene cursors cannot be relocated through this adoption option. Target party rules
initialize subgroups at committed time. This is a configuration migration and
therefore records the normal migration ledger/digest change; scene-binding-only
commands do not change configuration pins.

## Contracts and verification

Regenerate the additive command schemas with
`uv run --frozen python -m scripts.encounter_scene_contracts`; use `--check` for drift.
No frontend command controls are added here; #327 owns the player/GM flows.

`tests/test_encounter_context.py` covers scene/subgroup ownership, safe spectator
splitting, concurrent groups, explicit/ambiguous/scene-less migration, authorization,
stale commands/generations, unchanged clocks/knowledge, retries, restart and replay.
Existing wave-9 combat/investigation barriers and wave-10/14 capture/rescue paths
remain regression gates.
