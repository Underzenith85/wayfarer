# Tactical hex geometry (#105)

`wayfarer.simulation.hex_geometry` supplies immutable, deterministic geometry
contracts for the existing combat service. It performs no I/O, rolls no dice,
changes no campaign state, and grants no authority or visibility to a caller.

## Profile and persistence boundary

`HexBattlefield` requires all three tags: `coordinate_system="hex-axial-v1"`,
`profile_id="gurps-basic-set-4e-2004"`, and the exact conformance `baseline_id`.
There is no default or automatic conversion from the prototype's square grid.
Its existing `Battlefield`, Manhattan distance, four facings, JSON checkpoints,
and frozen v1 schemas retain their existing meanings. Hex snapshots round-trip
through the existing strict, frozen `Record` model. Unknown tags, duplicate cells,
and extra fields are rejected. An absent cell is outside the map.

The current GURPS profiles remain fail-closed. All three tactical capabilities
are **partial**, visible through the existing conformance registry to validators;
these primitives do not certify full tactical combat or enable campaign selection.
#115 owns typed API/client integration and explicit map migration, using the
existing campaign CAS, command receipts, authority checks, and visibility filters.
Do not feed a square `GridPoint` into hex movement or switch distance functions
based only on a requested action. The consumer must resolve the saved exact rules
profile before constructing a tagged hex battlefield.

## Coordinate and result contracts

- Axial `(q,r)`, implicit cube coordinate `s=-q-r`, one hex = one yard.
- Facings 0–5 follow `(1,0), (0,1), (-1,1), (-1,0), (0,-1), (1,-1)`.
  Rendering may rotate the map but must preserve this ordering.
- The three forward neighbors are front; the next clockwise neighbor is right,
  the opposite neighbor rear, and the remaining neighbor left. Same hex is close.
- `movement` takes a path excluding its origin and returns origin, destination,
  total MP cost, path and baseline. Forward movement follows its direction;
  sideways/backward movement preserves facing. Optional `turns` specifies the
  facing before each segment; each side turned costs one MP. A final one-side
  turn is free. Step permits any facing and costs one per flat ordinary hex.
- Posture-adjusted Move and Step use explicit integer rounding. Terrain adds an
  authored MP surcharge; it is not inferred from artwork. Stationary sitting or
  lying actors require posture resolution before translation. Rolling, jumping,
  climbing and multi-hex bodies are not synthesized by this primitive.
- Occupancy allows multiple actors in a close-combat hex. Entering one requires
  explicit consent from the combat consumer and must end the supplied path.
  Grappling, enemy obstruction/evading checks and subsequent close-combat choices
  belong to #108; this flag is not authorization to evade an enemy.
- `in_reach` accepts exact reachable distances, with C represented as 0. It tests
  distance and front/close arcs on level terrain. It does not select an attack,
  validate weapon readiness, or resolve an intervening obstacle.
- `can_retreat` tests a one-hex destination further from the attacker, occupancy,
  terrain, posture and supplied turn/condition restrictions. The consumer owns
  retreat history and defense bonuses; geometry cannot reset or spend them.
- Elevation and opaque height are integer yards relative to an explicit map datum.
  Movement across unequal elevations and melee across heights fail explicitly:
  the physical-feat/combat consumer must resolve those rules before changing pose.
  Elevation is used directly in geometric LOS.

## Source provenance and evidence

Frozen reference: **GURPS Basic Set: Campaigns, Fourth Edition, 2004 first
printing**, with the January 26, 2007
[errata](https://www.sjgames.com/errata/gurps/4e/basic-set-campaigns.html).
Page references for independent expected-result tests: B367–368 (posture/Step),
B377 (retreat restrictions), B384–387 (hexes, facing, movement), B388 (reach),
B391–392 (retreat and close combat). Tests contain original numeric cases, not
copied examples or rulebook prose. `tests/test_hex_geometry.py` also checks
serialization, failed paths and metric/LOS properties.

The source-artifact audit identified in #95 remains outstanding. Page locators
and engine fixtures are not evidence that the selected printing has been fully
reviewed. The tactical rows remain partial pending that review and integration;
they must not be promoted just because these tests pass.

The following geometric conventions are explicitly **engine policy**, not claims
about a published numeric example:

- Distant arc sectors use nearest hex directions; exact sector boundaries use the
  less favorable arc for the defender.
- LOS is an exact rational center-to-center ray clipped against closed hexes.
  Both hexes at an edge can block. Missing cells are opaque. An authored opaque
  column or raised ground touching the ray blocks it; movement blocking alone
  does not imply opacity. Heights are caller-authored, not inferred from posture.
- LOS samples a bounded corridor and performs exact hex intersection tests;
  results are symmetric under reversing endpoints, including edge ties.

Geometric LOS does not implement darkness, concealment, hearing, perception,
partial cover, or information disclosure. The existing visibility boundary must
still filter results before the LLM or another player can see them.
