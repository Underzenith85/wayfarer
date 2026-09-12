# Tactical hex geometry (#105)

`wayfarer.engine.simulation.hex_geometry` supplies immutable, deterministic geometry
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
these primitives do not certify the wider Basic Set combat catalog or enable
campaign selection. #115 supplied typed API/client integration and explicit map
migration using the existing campaign CAS, command receipts, authority checks,
and visibility filters.
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
  facing before each segment; each side turned costs one MP. Final facing follows the maneuver: Move permits any facing through half the
  budget and one side afterward; All-Out Attack permits one side; increased
  Dodge permits any facing. Step permits any facing and ignores terrain MP costs.
- Tactical posture costs are added per hex using exact decimal half-points,
  rather than multiplying the entire movement budget. A lying figure can move
  one hex using its budget; sitting requires a posture change. Positive Move
  guarantees one otherwise-legal hex despite cost penalties. Terrain adds an
  authored surcharge. Jumping, climbing and multi-hex footprints are not
  synthesized by this primitive; their action consumers still own authorization.
- Occupancy allows multiple actors in a close-combat hex. Entering one requires
  explicit consent from the combat consumer and must end the supplied path.
  Grappling, enemy obstruction/evading checks and subsequent close-combat choices
  belong to #108; this flag is not authorization to evade an enemy.
- `in_reach` accepts exact reachable distances, with C represented as 0. It tests
  horizontal distance, front/close arcs and effective vertical separation. Each
  reach yard beyond the first reduces the attacker's vertical separation by
  one yard (B403). It does not select an attack,
  validate weapon readiness, or resolve an intervening obstacle.
- `can_retreat` tests a one-hex destination further from the attacker, occupancy,
  terrain, posture and supplied turn/condition restrictions. The consumer owns
  retreat history and defense bonuses; geometry cannot reset or spend them.
- Elevation retains its existing integer-yard datum and adds optional
  `elevation_inches` (0-35). Old maps retain exactly their original elevations.
  Geometric LOS uses the exact combined value. Authored adjacent `stairs` edges
  permit ascent/descent with one additional MP per hex; an unmarked height
  transition still requires a physical-feat action. Empty stairs and zero offsets
  are omitted from serialization to preserve old migration command receipts.
- Armed and unarmed melee use B402-403 height bands regardless of posture:
  attack/location adjustments,
  inaccessible body parts, and active-defense adjustments. A parry uses its own
  selected weapon reach to reduce effective separation. Random targeting rejects
  height bands with inaccessible locations before any dice. Unarmed Dodge and
  Parry receive the same level modifier as armed defenses. Above six effective
  feet, an explicit special-position action is required; the engine does not
  infer geometry from posture alone.
- Ranged attacks use current axial ground distance and B407 elevation adjustments:
  uphill shots add the full rise, while downhill shots subtract half the drop to
  a floor of half the ground distance. Modes marked as tight-beam attacks ignore
  that adjustment; map geometry does not guess a weapon's projectile type.
- Authored stairs are the tactical elevation-transition primitive. Climbing,
  jumping, falling, and their rolls/consequences belong to the physical-feat
  service under #110; the tactical adapter continues to reject an unproven
  elevation change instead of treating a client-supplied path as authorization.

## Source provenance and evidence

Frozen reference: **GURPS Basic Set: Campaigns, Fourth Edition, 2004 first
printing**, with the January 26, 2007
[errata](https://www.sjgames.com/errata/gurps/4e/basic-set-campaigns.html).
Page references for independent expected-result tests: B367–368 (posture/Step),
B377 (retreat restrictions), B384–387 (hexes, facing, movement), B388 (reach),
B391–392 (retreat and close combat), B402–403 (height and reach), and B407
(uphill/downhill ranged distance). Tests contain original numeric cases, not
copied examples or rulebook prose. `tests/test_hex_geometry.py` also checks
serialization, failed paths and metric/LOS properties.

The frozen-source inventory and audit disposition were completed in #191. The
tactical rows remain partial because the broader combat and physical-feat
capabilities retain their own explicit blockers; they must not be promoted merely
because this bounded geometry contract is complete.

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

## Follow-up evidence (#105/#107)

`tests/test_geometry_injury_followups.py` and `tests/test_tactical.py` check inch
boundaries, each height band, asymmetric reach, movement costs, stairs,
non-standing and unarmed level combat, effective ranged distance, and saved
attack/defense sequences. The tactical capabilities remain partial until their
broader owning combat families are certified; no unfinished #105 behavior is
silently represented as supported.
