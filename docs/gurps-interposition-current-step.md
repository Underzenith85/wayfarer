# Current Move in ground Sacrificial Dodge (#878)

The selected Campaigns fourth printing B375 permits Sacrificial Dodge for a
friend within a Step. B368 defines a Step as ceil(Move/10), minimum one yard:
Move 1–10 permits one yard and Move 11–20 permits two. B419 and B426 halve
current Move below one-third HP or FP, rounding up; their reductions compound.
The existing actor movement view already applies current approved build,
carried load, injury, fatigue and supported movement effects.

The private `interposition-current-step` combat feature supplies this current
Move to the existing ground interposition Step admission. The cached encounter
allowance previously remained from the protecting actor's earlier turn, or the
encounter's initial default, until that actor acted again. A protector with
purchased Move 11 and a captured allowance of 11 who drops from 10 FP to 3 FP
now has current Move 6 and cannot interpose across two yards. The same is true
below one-third HP. Both reductions produce Move 3. A one-yard interposition
remains possible, and an unimpaired Move-11 protector can still cross two yards.
The selected square or hex route, terrain, occupancy, sight, Rooted Feet,
grip restrictions, enemy roll, friend defense and actual injury reducers remain
the existing consumers.

Fresh direct defense and typed owner-damage preparation commands capture the
feature. Historical recorded absence preserves cached-allowance admission.
Exact retries and seeded reexecution retain that captured policy after fresh
features change. `tests/test_interposition_current_step.py` covers actual
fatigue/injury producers, square and hex two-yard refusal before dice, lawful
successful movement/injury, authority, stale revision, exact retry/restart,
event folding and direct/typed seed-only reexecution on SQLite and PostgreSQL.
Reexecution begins from the persisted pre-response snapshot; this evidence does
not claim a whole original-genesis campaign proof.

The older terrain-route regression now buys actual Move 11 in its approved
fixture rather than granting only a synthetic cached allowance. This preserves
its two-yard route oracle under the corrected current-state policy.

## Retained grips and actual displacement

B370 makes letting go a free action on the holder's own turn. B371 describes
moving a pinned victim as a separate dragging/carrying action with encumbrance.
Neither creates an automatic out-of-turn release or an unqualified drag during
Sacrificial Dodge. The same fresh generation therefore refuses a ground
interposition that would change position while the protecting actor retains a
grip. This is consistent with the existing canonical hex Step and ground Dive
boundary. It does not release the victim, move the victim or charge inferred
encumbrance. Release/drag policies outside that boundary remain unsupported.

A holder already sharing the friend's position can still interpose without
translation. `tests/test_interposition_retained_grip.py` seeds four ordinary
approved actors, then creates the retained grip(s) through actual grapple and
defense commands. Both mapped geometries and both stores exercise refusal
before dice when movement is required and actual protecting-actor injury with
unchanged positions/grips when it is not. The co-located response has event-fold
and seeded pre-response-snapshot reexecution coverage. The old square admission
attempted dice before ultimately rejecting inconsistent separated close-combat
state; recorded feature absence retains that policy. Rejected commands leave
checkpoint, history, positions and grips unchanged. This correction concerns
admission timing and explicit movement policy, not a claim that invalid separated
grips previously committed successfully.

## Why the aerial/swimming concealment branch remains unsupported

B377 permits Flying/Swimming Dodge and Drop only when one Step takes the actor
below concealing terrain; it does not leave the actor prone. Ordinary
`ChooseDefense` has no selected drop destination, altitude or depth. Square
battlefields represent blocked XY cells only. Hex battlefields represent ground
elevation and opaque height, but their ordinary combat sight consumer uses
posture height and does not join the flyer's `PersonalFlightState.altitude`.
There is no combat swimming depth/medium pose. Existing two-dimensional blast
`GroundPosition` and the blast's water environment do not establish that
concealment geometry. A lawful consumer therefore needs an authored destination
and medium-specific current movement/sight contract before this branch can be
admitted. A player-supplied concealment flag would not establish it.

This ground correction preserves existing airborne interposition admission and
does not claim to implement aerial/swimming Steps, concealment, or other #878
specialized attacks. No source-ledger or certification status is promoted.
