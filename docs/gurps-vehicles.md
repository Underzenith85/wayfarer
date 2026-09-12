# Basic Set vehicle operation audit (#207, #358)

This adapter extends the #208 transport foundation inside the existing resource
transaction. It does not supply the live mounted/vehicle encounter integration
originally assigned to #120. Full vehicle movement/combat capabilities remain
**partial**. #207 closed without completing them, so #358 owns the two
capability rows and audits what each mode still owes; see
[the residual blockers](#residual-coverage-blockers-358) below.

## Rules selection and authority

Transport `mechanics_version=1` keeps the original commands and behavior. New
operation commands require `mechanics_version=2`. A trusted scenario may explicitly
create a version-two transport; an existing stopped, controlled wheeled vehicle
can use `vehicle-upgrade-v2`. The upgrade consumes one revision, preserves HP and
inventory, and is receipt-idempotent. New locomotion tags reject under version one;
legacy commands reject a version-two transport. There is no automatic migration.

Commands remain internal: `ResourceService.execute_transport` authenticates the
operator and requires engine authority. Character-derived HT, armor, innate DR,
restraints and ST are trusted caller inputs, never player-authored damage powers.
Collision target vehicles are validated too. Both bodies, all passenger injuries,
control state and traces commit under the same campaign CAS revision. Replays use
receipts without drawing dice or applying wounds again.

The existing `InitialResources` boundary still rejects transports in frozen v1
scenario documents, and live play still rejects transport-bearing checkpoints.
`rules.vehicle_capabilities.VEHICLE_OPERATIONS` exposes the actual internal
operation set per locomotion tag. It is not permission to activate a complete
GURPS profile or let scenario generation invent the remaining mechanics.

## Source and independent evidence

Source: the selected GURPS Basic Set: Campaigns Fourth Edition, fourth printing,
B394-395, B430-432 and B468-470. Item-level mechanics review remains part of
#191; this PR does not certify the full vehicle rules.

`tests/test_vehicle_modes.py` supplies independently entered expected values:
B432's HP60/velocity25 versus HP10/velocity5 rear-end example produces 12d and 2d;
other cases cover unequal/equal-speed head-on caps, side-on caps, armor versus
innate DR, individual restraints, breakable-object caps, and mode-specific control.
SQLite and PostgreSQL transaction tests cover concurrent duplicate collision and
restart. PostgreSQL runs require `WAYFARER_TEST_DATABASE_URL`.

## Implemented internal operations

| Area | Executable behavior | Source |
| --- | --- | --- |
| Additional ground locomotion | Tracked, animal-drawn, walking and slithering tags; acceleration limits and safe braking of 10 yards/second versus 5 for powered wheels | B468 |
| Air/water braking | Maximum of 1 and 5 + Handling | B468 |
| Planar navigation | Authored full courses on the existing hex map; longitudinal or rotated two-dimensional footprints; occupancy and swept-turn obstruction checks | B394-395 |
| High-speed movement | Full ordinary move before acceleration into high speed; saved velocity budget thereafter; turning radius carried across turns; early/tight-turn and emergency-braking control checks | B394-395 |
| Terrain | Authored extra movement costs consume the velocity budget and reduce end speed; risky braking checks within the supported envelope | B395 |
| Water/air geometry | Level flight at a saved altitude; surface draft checks and submerged clearance against an authored waterline and bottom | B466, B468 |
| Ground control | Margin/SR split, random left/right veer outside turns, remaining skid movement and mapped straight skid resolution | B469 |
| Rollovers | Mapped straight roll/skid distance of velocity/3; integer hex position plus saved fractional thirds; body/passenger falling damage | B469, B431-432 |
| Air control | Minor altitude/speed loss, minimum-speed stall, severe dive/stall state, and Piloting-5 recovery checks | B469 |
| Water control | Drift, capsize for unsinkable craft, or sinking state | B469 |
| Space/submarine control | Drift; submarines lose depth on minor failures; severe failures roll object HT and persist stress-failure state | B469 |
| Collision exchange | Head-on/rear-end/side-on relative velocities and faster/striking-body dice caps; each body uses the existing object-damage reducer | B430, B432 |
| Immovable obstacles | Hard/soft surface factor; optional authoritative breakable object limits both damage amounts to obstacle HP + DR | B431 |
| Occupants | Damage based on each vehicle's actual speed loss; per-occupant belts/airbags, worn armor blunt trauma and innate DR; existing injury/threshold reducer | B431-432 |
| Open cabin | Unbelted passenger knockback distance uses pre-armor damage and explicitly compiled ST; persisted ejection-pending state blocks subsequent movement | B432 |
| Ground slopes and overland terrain | Tactical slopes require an authored movement surcharge; B466 cruising-speed factors distinguish wheeled, tracked and legged vehicles and cap road-bound off-road speed | B395, B466 |
| Ground skid aftermath | Difficult terrain consumes saved skid movement; declared vehicles and actors enter the existing collision/injury path at the last clear pose | B395, B430-432, B469 |
| Ejection aftermath | Pending ejections leave the manifest, resolve to the exact knockback hex, take landing collision damage, and enter the existing drowning/swimming/rescue schedule after water entry | B354, B431-432, B436 |

## Residual coverage blockers (#358)

#120 closed after landing a ground slice and #207 closed after expanding the
modes, so neither is available to own what is still missing. #358 audits the
residual per locomotion mode and splits it into live children;
`rules/vehicle_coverage.py` carries that audit as typed data, and
`vehicle_coverage.audit_report` publishes it. A pending state is not completion.

| Missing consumer or variant | Current behavior | Owner |
| --- | --- | ---: |
| Vertical flight, climbing/diving trajectories, continuing stalls/falls, airborne drift and terrain-relative air-crash consequences | Altitude/control facts persist, but full three-dimensional movement and ongoing descent are not implemented. | #393 |
| Sinking, capsizing recovery, underwater stress damage, leaks, decompression, water currents, fractional draft and open-deck overboard checks | Pending states block ordinary operation; open-deck control rejects before dice. | #394 |
| Space thrust, navigation, fuel/delta-v and very large speed/damage scales | Navigation rejects; only control/stress and the resolved collision exchange are provided. | #395 |
| Mounted movement, Riding control against the mounted loss table and rider separation | `ground-mount` carries no version-two operation at all; every path rejects by name. | #396 |
| Ramming attack/defense, mounted weapons, cover, Aim and penalty consumption, and synchronized encounter poses | Live integration originally assigned to #120 remains missing despite that issue's closure. | #397 |
| Vehicle hit locations, operator incapacitation, ongoing stress below zero HP and disabled equipment effects | Existing object damage is reused; these live consumers are not distinguished. | #397 |

No unsupported outcome is replaced with an LLM ruling, default damage, random
invented malfunction or a full-coverage claim. #105/#107 and #181 remain dependency
owners for geometry, injury and live durability integration. The bounded tests
recorded here establish only the operations listed in the implemented table
above, never the residuals.

## Declared capability rows (#358)

`gurps.vehicles.movement` and `gurps.vehicles.combat` are owned by #358 and their
status is **derived** from the audit above rather than hand-set: a mode counts as
verified only once it resolves control loss, collision, occupant injury and
restart and owes no residual, and the movement row is verified only when every
mode is. The five non-mounted ground modes now qualify. Air, water, underwater,
space and mounted movement keep the movement row `partial`; the combat row stays
`partial` as well.

`rules/vehicle_coverage.validate_coverage` rejects three drifts: an audit that
declares different modes from `VEHICLE_OPERATIONS`, a mode that claims a concern
it carries no operation for, and a residual whose owner is this audit itself or
one of the closed issues it supersedes. It also rejects a declared capability
status or owner that disagrees with the audit, so raising a mode is the only way
to raise a row.

Every bound #346 vehicle skill records `gurps.vehicles.movement` as an activation
blocker, and `rules/mundane_skills/technology.unsupported_scope` publishes it with
#358 as its owner, so the scenario, character and LLM validators see the gap
rather than inferring support. Repairing a machine is not operating one, so the
#356 Mechanic rows carry no activation blocker. Once the row reaches `verified`,
those procedures drop the blocker with no change to the skill side.

Evidence is in `tests/test_vehicle_coverage.py`.
