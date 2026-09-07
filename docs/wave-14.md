# Wave 14 — The Last Lantern

The installed reference adventure is `last-lantern-1`, with the authored successor
`last-lantern-2` (A Favor Repaid). Both are versioned ScenarioGraph JSON files under
`src/wayfarer/adventures/fixtures/`. `lantern.build_adventure()` is their reproducible
authoring source; a regression test requires exact model equality with the files.
The reference uses original prototype rules and a pinned `wayfarer-reference-1`
package. It does not require licensed rule text or live provider credentials.

## Run and start a game

Build the frontend with `pnpm --dir frontend build`, then run:

```sh
uv run python -m wayfarer.adventures --tokens /private/tokens.json --db data/lantern.sqlite3 --frontend frontend/dist
```

The token file maps your chosen bearer tokens to principal IDs. Include two player
identities and an identity named `gm`; the server supplies no default credentials.
Open `http://127.0.0.1:8000`. Each player uses their own token in a separate browser
profile. Sign in, choose The Last Lantern, create a setup, and save the authored
graph. Invite the other principal, have them join, assign Mira (`a`) and Iven
(`b`) to separate players, and have both mark ready before activation.
The warden is an NPC, validated with the same conservative character policy and
controlled only by the configured GM. The GM is never a player-character seat.

Open the playing scene. Scene decisions use validated commands directly, including
inspection, encounter choices, travel, waiting and recovery. Model-backed text,
reviewed speech and generated adventures additionally require the existing provider
configuration. The authored template and successor work without it. Dictation is a
reviewable draft; only Send action submits it.

## Routes and outcomes

The ferry leaves at shared tick 12. Mira must discover the stolen relief manifest,
secure passage and retain her cutlass. Inspect the shipping manifest, or travel the
customs path for the duplicate records (split first, with Iven waiting to advance
each shared travel tick, then return and rejoin). Begin the parley and appeal to duty to
negotiate passage. Optional combat in `guardhouse-yard` can secure the same passage
by incapacitating Warden Sable through resolved injury. A GM-declared encounter end
does not grant the combat reward. NPC patrol scheduling advances with shared time.

Both objectives give success; one gives partial success at the deadline; neither
gives failure. Rewards are applied once. Complete the campaign in the lobby to save
its epilogue, select A Favor Repaid, save its preview, and continue. Character
injuries, equipment, commitments and custody persist; continuing is not a free heal.
`camp-rest` is an authored downtime choice. Fresh clue IDs prevent the previous
adventure's knowledge from immediately completing the successor.

The optional combat branch uses the authenticated `/campaigns/{id}/commands` API
for GM encounter placement and player attack maneuvers; the player UI exposes
defense choices. Every command supplies a unique `id`, controlled `actor_id`, and
the latest `expected_revision`. See `test_reference_combat_route_resumes_defense_and_never_rerolls`
in `tests/test_wave14.py` for the full executable sequence. Retrying the same command
ID replays its recorded result without rolling or rewarding again.

## Two-player capture and rescue

1. Mira selects Split from group. The authenticated GM applies `harbor-capture` to
   `a` using `apply_setback`. Her gear is confiscated; capture leaves the adventure
   ongoing. Iven cannot see her private cell knowledge or unexplained rescue choices.
2. Mira chooses `study-cell` while Iven waits one tick. Mira then chooses
   `signal-outside` while Iven waits again. The explicit signal reveals detention
   to Iven, without sharing the loose-shutter clue.
3. Iven chooses `quiet-rescue` (stealth) or `negotiate-release` (diplomacy). Mira
   chooses `loosen-bars`. Each subgroup must commit its activity before shared time
   advances. A disconnected captive does not silently choose an action.
4. After release, Mira chooses `recover-gear` while Iven waits. Then explicitly
   rejoin `group:harbor-scene`. Release alone does not return confiscated inventory.

Mira may instead attempt `slip-bonds`; failed attempts cost time. The tests cover
both partial success and failure while still captive, including continuation.
They also cover a rescuer joining an ongoing combat only after a pending defense
resolves, rescue during that encounter, and explicit reunion without resetting it.

## Acceptance evidence

`uv run pytest tests/test_wave14.py` exercises setup, legal activation, authenticated
commands, durable restart, concurrent recovery choices, rewards, epilogues and
continuation through HTTP. It asserts private projections, exact replay and no
rerolls. No test seeds a playable campaign through a private mutation endpoint.

The finite provider in `tests/reference_provider.py` supplies generated graphs and
narration through the normal provider interface. Generated drafts go through the
same validator and activation path as authored graphs. The validator's reachability
closure includes supported noncombat and recovery clue sources; it is a structural
check, not a proof of every tactical outcome.

```sh
cd frontend
pnpm exec playwright test --config playwright.reference.config.ts
```

This separate CI suite runs the real reference server with deterministic dice and
provider responses. Two browser contexts cover reviewed voice, saved negotiation,
epilogue and successor, plus split/capture/rescue, reconnect, gear and private reunion.
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` optionally selects an existing local Chromium.
The longer staged browser-play session in issue #59 remains supplemental.
