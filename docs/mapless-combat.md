# Basic (mapless) combat

Basic combat is an executable encounter representation that does not create a
square or hex map. `StartBasicEncounter` binds the fight to an authored scene,
names its participants and supplies scenario-owned spatial facts. The persisted
`BasicSpatialContext` is the only spatial owner; combatants have no serialized
position or facing.

The authoritative fact vocabulary covers directed visibility, cover, obstacles,
reach/engagement and retreat feasibility, plus symmetric distance. Every fact has
a scenario, GM-adjudication or engine-derived provenance record and an explicit
revision lifetime. There can be only one active fact for each normalized key.
Replacement declarations invalidate the prior value rather than rewriting
history. Missing, conflicting, stale and player-authored judgments fail closed.

`BasicMove` names another participant as the reference and chooses approach or
withdraw. Move uses the actor's movement allowance; maneuvers which allow a Step
use `ceil(Move / 10)`, with a minimum of one yard. Movement derives a new distance
and reach fact and invalidates the mover's other spatial facts. Consequently,
visibility, cover, obstacles and retreat feasibility must be adjudicated again
before a later action relies on them. A movement-and-spatial-action combination
which would need facts about its resulting state is rejected rather than using
pre-movement facts.

Melee and unarmed legality consume the active reach/distance facts. Ranged
situations retain speed and Size Modifier but derive their distance from the
active distance fact, so they cannot hold a second, stale distance. Full cover
blocks targeting. A Basic retreat requires a directed feasible-retreat fact and
the normal active-defense, posture, stun, control and once-per-turn checks; the
retreat then performs a mapless Step and invalidates affected facts.

The commands use the normal authorization, compare-and-set, durable receipt,
restart and replay paths. GM declarations must identify their command as the
provenance source and match its committed revision. Basic encounter state remains
scene- and subgroup-validated.

[Reinforcement admission and Basic-to-hex escalation](combat-reinforcements.md)
are implemented by #326. Tactical controls are owned by #327. Hex-to-Basic
conversion is implemented by #329; withdrawals remain owned by #330.

## Hex-to-Basic conversion

`MigrateEncounterBasic` is an explicit GM command at an active, authored scene.
It derives distance plus both directions of reach, visibility, cover, obstacle and
retreat facts from the current exact poses. Same-hex close-combat pairs remain
`close`, and grips and other nonspatial encounter state remain unchanged. The
derived facts identify the conversion command and revision as their provenance.

Conversion is lossless rather than a request to hide the map. It accepts a flat,
fully traversable battlefield with no darkness or movement surcharge. Authored
blocking/opaque/elevated terrain, stairs, grounded equipment, unresolved spatial
explosions, positioned spell effects, interrupted Waits and pending post-attack
hex movement reject explicitly. A pending defense without queued hex movement is
preserved exactly; conversion does not recompute its allowed defenses or refresh
any participant's defense usage.

The map template remains in the rules configuration for other encounters, while
the converted encounter owns only Basic facts and no serialized poses. A later
Basic-to-hex command must again supply every pose and pass the normal consistency
checks. Retry, restart and replay use the same command receipt and campaign CAS.

## Source boundary

This implementation is bounded to *GURPS Basic Set: Campaigns*, pp. B367-B368
(combat without a tactical map and the Step calculation) and p. B377 (retreat
movement and restrictions), using the repository's reviewed later-printing
baseline. These references establish the independent examples in
`tests/test_basic_combat.py`; they do not promote the wider Basic Set profile or
uncited tactical edge cases to verified status.
