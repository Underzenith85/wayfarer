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

Representation-specific reinforcement placement and Basic-to-hex migration are
owned by #326. Tactical controls are owned by #327. Close-combat representation
transitions and withdrawals are owned by #329 and #330. Until those contracts are
implemented, the corresponding coordinate-dependent paths reject explicitly.

## Source boundary

This implementation is bounded to *GURPS Basic Set: Campaigns*, pp. B367-B368
(combat without a tactical map and the Step calculation) and p. B377 (retreat
movement and restrictions), using the repository's reviewed later-printing
baseline. These references establish the independent examples in
`tests/test_basic_combat.py`; they do not promote the wider Basic Set profile or
uncited tactical edge cases to verified status.
