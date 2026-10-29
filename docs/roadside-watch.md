# Roadside Watch

Roadside Watch is a small GURPS Fourth Edition campaign for two fixed human characters and a GM-controlled brigand, validated through its HTTP API. At tested source `7713b402f49e9ccbb42edd3227e53a66bf0dc339`, the complete official regression passed 13223 ordinary tests with 1 allowed optional-provider skip, 16191 architecture subtests and all 22 static/extra gates. All 66 real campaign cases passed across SQLite and PostgreSQL. The unchanged normal CI release scope passed. The additional strict global source-completeness gate failed with 117 preserved blockers; full Basic Set certification remains incomplete. Browser play is unverified; genuine audit status is in [checkpoint evidence](roadside-watch-checkpoint-2026-10-29.json).

The earlier standalone 66-case run remains bound to 37583b90 and source digest 7772689b32095d69a3b3807cec7c82f9a4ee722a713b24d4699b45ea67f03dea; fresh V4 a561 66-case proof is separately recorded within a failed whole-suite run. Neither prior source is relabeled as the final source. Generic continuation repairs preserve wider repository compatibility but do not add sequel commands to this fixed API. B421 concentration classification remains explicitly ambiguous; magic is excluded.

## Campaign scope

One player identity, `party`, controls Mira (`a`) and Iven (`b`). A separate
identity, `gm`, controls Rook (`brigand`) and encounter administration. The module
is public: its starting NPC facts are not secret GM material.

| Character | Verified source build | Starting resources |
| --- | --- | --- |
| Mira | 100 points; ST11 DX12 IQ11 HT11; Broadsword14, Stealth13, Observation13 | HP11, FP11; broadsword, leather armor; 13lb carried |
| Iven | 100 points; ST10 DX11 IQ12 HT11; First Aid/TL3 13, Broadsword12, Observation15, Stealth14 | HP10, FP11; broadsword, leather armor, sterile bandages; 15lb carried |

Both characters have explicit human anatomy and sword grips. Neither begins with
an artificial wound or FP deficit. The module uses its own immutable TL3 rules
composition; it does not change the existing Basic Set profiles.

Admitted play consists of the road Observation check, optional unopposed Stealth,
scene travel, ordinary sword combat, Move, Ready, Do Nothing, posture changes,
Dodge, Parry, no defense, legal retreat, basic bandaging, First Aid and FP rest.
Optional Stealth records a quiet or noisy approach; it does not grant
surprise, concealment or an attack bonus. The encounter is mutually aware.
Battles are limited to temperate conditions,
the fixed three-actor roster and at most 119 elapsed combat seconds. Read the
current tactical state and end the encounter before a turn would exceed that
limit. Automatic encounter completion is excluded. Parry
requires an explicit sword mode (`swing` or `thrust`) so critical results have
an unambiguous context.

Magic, ranged attacks, shields, unarmed combat, custom character creation,
advantages, powers, trade, advancement, natural healing, Diagnosis and generic
medical equipment are excluded. The canonical First Aid Kit's +1 modifier is not
consumed by this medical path, so the kit is excluded. Ordinary sterile bandages
provide +0; they are not a renamed kit. Unsupported mutations are rejected by the
module's HTTP admission boundary and native command validation.

The coverage matrix must distinguish supported behavior from partial,
unsupported and unverified behavior. Catalog labels and a passing build alone do
not establish campaign coverage or full Basic Set compliance.

Observation and Stealth retain their actual critical roll flags. The human GM
narrates additional critical good/bad effects and reactions; this module does
not automatically persist extra reactions, grant bonuses or implement a general
GM consequence engine. Treat that portion of skill adjudication as partial
automation. Severe injury, crippling or death can end the episode in failure.
There is no carrying, resurrection, replacement character or long-term
rehabilitation route in this scope. If severe injury prevents a safe return,
the GM can record abandonment before the return objective completes, without
erasing those injuries. Observation/Stealth critical narration is manual fiction;
it is not a persisted mechanical modifier or an auditable consequence command.

## Run locally

Install the repository's pinned dependencies. Create an owner-only JSON file
mapping two different private bearer tokens to `party` and `gm`; do not commit it:

```json
{"<private-player-token>": "party", "<different-private-gm-token>": "gm"}
```

```sh
chmod 600 /path/to/private-tokens.json
uv run python -m wayfarer.adventures.roadwatch_cli \
  --tokens /path/to/private-tokens.json --db data/roadwatch.sqlite3
```

This CLI launches the HTTP server; it is not an interactive game interface.
All V1 mutations are disabled. Admitted writes use the campaign commands or
Tactical V2 routes. The server listens on `127.0.0.1:8000`. Requests use
`Authorization: Bearer <token>`; POST bodies are JSON with
`Content-Type: application/json`. For PostgreSQL, configure the database URL in
`WAYFARER_DATABASE_URL`, then add `--backend postgres --v1-ledger
/path/to/roadwatch-receipts.sqlite3`. Keep the receipt ledger durable alongside
the campaign database. Never discard either to resolve a retry conflict.

## Reproduce the scenario through the API

1. As `party`, GET `/setups/templates`. Use the returned Roadside Watch graph and
   its brief unchanged in POST `/setups` with a fresh `id`. The exact graph is
   validated. Use the returned campaign ID; do not assume it equals the request ID.
   The portable scenario format is not used for these anatomy/grip
   guarantees.
2. POST `/setups/{campaign_id}` successively with operations `assign` (principal
   `party` (`principal_id`), actors `["a", "b"]` (`actor_ids`)), `ready`, and
   `activate`. Every request needs a
   fresh `id` and the current `expected_revision` from the previous response.
   The trusted composition seats the GM separately.
3. GET `/campaigns/{campaign_id}` for party state and current recovery choices.
   Send gameplay to POST `/campaigns/{campaign_id}/commands` with `kind`, a fresh
   `id`, `actor_id`, `expected_revision`, and the action's fields. Keep an accepted
   payload unchanged when retrying its ID. Changed intent needs a new ID.
4. Iven can `inspect` target `road-view`. Optional Stealth uses `start_noncombat`
   selection `quiet-path` and a new `encounter_id`, followed by
   `approach_noncombat` using that same `encounter_id` and selection
   `move-quietly`, or `withdraw_noncombat` with the same encounter ID.
   Failed optional checks do not prevent
   withdrawing or completing the shelter objective.
5. Move both PCs using `travel_scene`, exit `post-to-road`. Use the tactical
   projection GET `/api/tactical/v2/campaigns/{campaign_id}?actor_id=a` for current
   combat state. The GM starts the fixed three-actor encounter on
   `roadwatch-clearing`, scene `road-scene`; use the exact placement/team payload
   below.
6. Play ordinary turns and defenses using current revisions. The GM can end the
   encounter with an authored reason. Both PCs then use `road-to-shelter`.
7. If genuinely injured, select the current opaque `gurps_recovery_choices` ID
   for Iven to bandage the patient. Submit `gurps_recovery` with that `choice_id`.
   Send `wait` with actor `a` and `ticks: 60`, then use the offered finish
   choice as actor `b`. Begin First Aid from its
   new offered choice, send `wait` with `ticks: 1800` at TL3, then finish.
   This scenario requires Iven to bandage before First Aid, at the shelter;
   both PCs have the same controller. Bandages cover six distinct wounds.
   Do not construct
   opaque IDs, prescribe dice outcomes or edit HP directly. Travel during active
   care must interrupt it; already earned care must be settled first.
   For actual battle FP loss, select the patient's own `rest` recovery choice,
   wait with `ticks: 600`, then use the current finish choice as that patient.
   Full FP must not permit a banked future rest.
8. The return objective completes automatically when both PCs reach shelter.
   Clinical care and FP rest can continue after this success; they are not
   victory conditions. `evaluate_objectives` can request an explicit objective
   checkpoint, but is not required to complete the return. For a failure ending,
   the GM sends `abandon_scenario` as actor `gm`, using the GM token, while the
   objective outcome is still ongoing. Abandon before both PCs reach shelter;
   it does not overwrite an existing success. The authored deadline is 86,400
   game seconds. Optional failed checks do not require a fabricated success.

The GM's authenticated GET `/roadwatch/campaigns/{campaign_id}/gm` supplies real
receipt and pending-critical context. Player and outsider identities cannot use
it. It is not a way to select dice results or bypass native continuation rules.

## Setup and scene payloads

For POST `/setups`, copy the complete template object returned by GET
`/setups/templates` into `graph`, copy its `brief` into `brief`, and provide a
fresh string `id`. This creation request does not take `expected_revision`.
Then POST these bodies individually to `/setups/{campaign_id}` using the returned
campaign ID, player token, and current revisions:

```json
{"id":"<fresh-id>","expected_revision":0,"operation":"assign","principal_id":"party","actor_ids":["a","b"]}
{"id":"<fresh-id>","expected_revision":1,"operation":"ready"}
{"id":"<fresh-id>","expected_revision":2,"operation":"activate"}
```

Gameplay examples below use the player token. Refresh the revision after every
accepted command. Each line is a separate request body. In these examples,
replace `"<current-integer>"` with an unquoted current integer and all other
placeholders with the indicated strings. Do not submit the examples as one JSON
array or reuse a command ID with changed intent.

```json
{"kind":"inspect","id":"<fresh-id>","actor_id":"b","expected_revision":"<current-integer>","target_id":"road-view"}
{"kind":"start_noncombat","id":"<fresh-id>","actor_id":"b","expected_revision":"<current-integer>","encounter_id":"quiet-survey","selection_id":"quiet-path"}
{"kind":"approach_noncombat","id":"<fresh-id>","actor_id":"b","expected_revision":"<current-integer>","encounter_id":"quiet-survey","selection_id":"move-quietly"}
{"kind":"travel_scene","id":"<fresh-id>","actor_id":"a","expected_revision":"<current-integer>","exit_id":"post-to-road"}
{"kind":"travel_scene","id":"<fresh-id>","actor_id":"b","expected_revision":"<current-integer>","exit_id":"post-to-road"}
```

Stealth is optional. To withdraw from a still-active optional encounter, use
`kind: "withdraw_noncombat"` with its `encounter_id` and omit `selection_id`.
Observe the returned outcome before making another approach; a completed
encounter does not need withdrawal. After combat ends, use the same two travel
bodies with `exit_id: "road-to-shelter"`.

## Concrete encounter payloads

These JSON bodies go to `/campaigns/{campaign_id}/commands`. Replace every ID
placeholder with a fresh command ID and every revision placeholder with the
current integer revision; they are notation, not literal valid JSON values.
The GM uses its own token. Positions below match the reproducible clearing setup.

```json
{
  "kind": "start_encounter", "id": "<fresh-id>", "actor_id": "gm",
  "expected_revision": "<current-integer>", "encounter_id": "roadwatch-fight",
  "battlefield_id": "roadwatch-clearing", "scene_id": "road-scene",
  "placements": [
    {"actor_id": "a", "position": {"q": 1, "r": 0}, "hex_facing": 0},
    {"actor_id": "b", "position": {"q": 0, "r": 1}, "hex_facing": 0},
    {"actor_id": "brigand", "position": {"q": 3, "r": 0}, "hex_facing": 3}
  ],
  "allegiances": [
    {"actor_id": "a", "side_id": "watch"},
    {"actor_id": "b", "side_id": "watch"},
    {"actor_id": "brigand", "side_id": "brigand"}
  ],
  "oppositions": [{"side_ids": ["brigand", "watch"]}]
}
```

In initial turn order Mira, Iven, Rook, send the following with a fresh ID and
revision each time. These are a demonstration of lawful choices, not a guaranteed
injury: the normal server rolls independently.

```json
{"kind":"take_combat_turn","id":"<fresh-id>","actor_id":"a","expected_revision":"<current-integer>","encounter_id":"roadwatch-fight","maneuver":"move","hex_path":[{"q":2,"r":0}],"hex_facing":0}
{"kind":"take_combat_turn","id":"<fresh-id>","actor_id":"b","expected_revision":"<current-integer>","encounter_id":"roadwatch-fight","maneuver":"do_nothing"}
{"kind":"take_combat_turn","id":"<fresh-id>","actor_id":"brigand","expected_revision":"<current-integer>","encounter_id":"roadwatch-fight","maneuver":"attack","item_id":"sword-brigand","mode_id":"swing","target_id":"a","hit_location":"torso"}
```

If the resulting public state requests Mira's defense, submit her chosen lawful
defense. For example, `choose_defense` with `encounter_id: "roadwatch-fight"` and
`defense: "dodge"`, plus the usual identity/revision fields. A critical attack can
resolve without a defense choice; inspect current state before submitting one.
Example defense uses the player token; the end command uses the GM token:

```json
{"kind":"choose_defense","id":"<fresh-id>","actor_id":"a","expected_revision":"<current-integer>","encounter_id":"roadwatch-fight","defense":"dodge"}
{"kind":"end_encounter","id":"<fresh-id>","actor_id":"gm","expected_revision":"<current-integer>","encounter_id":"roadwatch-fight","reason":"Rook breaks off; the volunteers withdraw to shelter."}
```

End only after the pending attack/defense is resolved. The PCs can then travel
to shelter. A blocked critical must be resolved by its
native supported continuation first; do not rewrite state to escape it.

## Care and endings payloads

Read `gurps_recovery_choices` from GET `/campaigns/{campaign_id}`. Choose Iven's
bandaging action for the actual patient's wound, then submit its opaque ID as
actor `b`. Care choices also require a capable caregiver; if no lawful choice
is offered, do not fabricate one. Do not copy an ID from another wound, patient
or revision. The same
command shape starts First Aid and finishes either procedure, using the newly
offered appropriate ID each time:

```json
{"kind":"gurps_recovery","id":"<fresh-id>","actor_id":"b","expected_revision":"<current-integer>","choice_id":"<current-offered-choice-id>"}
{"kind":"wait","id":"<fresh-id>","actor_id":"a","expected_revision":"<current-integer>","ticks":60}
```

After that wait, finish bandaging, start the offered First Aid action, wait
1,800 seconds using the same wait body with `ticks: 1800`, and finish First Aid.
These are separate requests with fresh IDs and current revisions. Do not begin
First Aid again after the attempt settles; ordinary failure is an outcome, not
permission to retry the same wound. If interrupted, use current offered choices
and task state rather than assuming the original finish remains lawful.

FP rest is personal: use the patient's own offered rest choice and actor ID,
wait 600 seconds, then use that actor's offered finish choice. Bandage capacity
is not a prerequisite for FP rest. A full-FP character cannot bank future rest.

For an ongoing scenario that must end in failure, the GM uses its token with:

```json
{"kind":"abandon_scenario","id":"<fresh-id>","actor_id":"gm","expected_revision":"<current-integer>"}
```

The ordinary success route is both PCs returning to shelter. It is automatic,
even if Observation/Stealth failed. Care may continue afterward. No ending
heals severe injury, carries an incapacitated character, revives a dead one or
replaces the fixed roster.

## Reproduce validation

`tests/test_roadwatch_campaign.py` and `tests/support/roadwatch_campaign.py` drive
the real HTTP application, stores and command pipeline. They do not seed played
wounds, FP deficits or caller-selected dice outcomes. Explicit trusted test seed
injection makes the actual rolls reproducible; the normal server uses native
entropy.

With an isolated local PostgreSQL test database configured:

```sh
export WAYFARER_TEST_DATABASE_URL='postgresql://<local-test-connection>'
uv run pytest --no-cov -q tests/test_roadwatch_campaign.py
```

The selected suite requires both SQLite and PostgreSQL; a missing database is
not a skip or coverage credit. The 66 passing acceptance cases exercise
source-derived values, continuous play,
authority and unsupported requests, exact retries, reload, concurrent updates,
rollback, event folding and original-seed reexecution of all postactivation
commands on the tested paths. This bounded evidence does not establish full
Basic Set coverage or certify every Basic Set rule. The complete engine gates at the tested source are recorded separately in the checkpoint manifest. Setup history
is folded and reloaded separately: the official executor
does not currently reexecute setup commands from seeds, and no such claim is made.

For the clinical source oracle, an actual four-HP sword injury leaves Mira at7;
bandaging raises her to8. TL3 First Aid heals a total of `max(1, 1d-3)` including
that bandaging point: a die6 raises her to10, not11. Critical success also reaches
10; ordinary failure leaves8 and critical failure leaves6. Positive FP-rest
coverage requires an actual battle-end FP charge first, never an edited deficit.
