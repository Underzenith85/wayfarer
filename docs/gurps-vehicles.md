# Basic Set vehicle operation audit (#207, #358)

This adapter extends the #208 transport foundation inside the existing resource
transaction. Vehicle movement and combat are **verified** after the bounded
completion work in #392-#397. #207 closed without completing them, so #358 owns the two
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
| Water/air geometry | Surface draft and submerged clearance; authored air climb/dive endpoints checked against terrain throughout the trajectory | B394-395, B466, B468 |
| Ground control | Margin/SR split, random left/right veer outside turns, remaining skid movement and mapped straight skid resolution | B469 |
| Rollovers | Mapped straight roll/skid distance of velocity/3; integer hex position plus saved fractional thirds; body/passenger falling damage | B469, B431-432 |
| Air control | Minor altitude/speed loss, minimum-speed stall, severe dive/stall state, and Piloting-5 recovery checks | B469 |
| Air aftermath | Minor blunders displace the aircraft; dives descend at Top Speed each turn; stalls accelerate downward; terrain contact uses the existing collision, object-damage and occupant-injury reducers | B430-432, B469 |
| Water casualties | Authored currents honor fractional draft; open-deck failures persist overboard occupants and drowning; sinking advances at saved hull/leak rates into shared breathing hazards; underwater leaks damage the hull and schedule pressure exposure; unsinkable craft can spend a turn righting | B435-437, B466, B469 |
| Water control | Drift, capsize for unsinkable craft, or sinking state | B469 |
| Space/submarine control | Drift; submarines lose depth on minor failures; severe failures roll object HT and persist stress-failure state | B469 |
| Space navigation | Reaction-drive burns consume an explicit delta-v pool and use the B466 acceleration time; coasting crosses authored hex courses at a declared miles-per-hex scale and records travel time; collision dice above the exact replay envelope reject before randomness | B430-432, B466-467 |
| Mounted operation | A mount stays a creature behind the transport adapter; its own Basic Move limits tactical movement, Riding resolves the B397 mount-loss table, consequences persist, and a separate command resolves rider/mount falls or a direct mounted collision through the existing injury reducer | B397, B430-432, B466-470 |
| Declared ramming | The operator makes a vehicle attack, the target separately chooses Dodge or no defense, and an undefended hit enters the existing two-body collision and occupant-injury reducers | B430-432 |
| Mounted weapons and cover | A vehicle-mounted ranged mode fires through the existing ranged attack/defense path only when the gunner, crew and authored hex pose match the carrier; saved control penalties and lost Aim are consumed by that attack, while authored occupant cover contributes DR | B396, B407-408, B469 |
| Vehicle locations and stress | Hull, motive, controls and mounted-weapon hits use existing object durability; penetrating subsystem hits disable their consumer, controls may injure the operator, and nonpositive hull HP invokes the existing per-turn B483 stress check | B483-484 |
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
`engine/rules/vehicle_coverage.py` carries that audit as typed data, and
`vehicle_coverage.audit_report` publishes it. A pending state is not completion.

There are no remaining vehicle movement or combat residuals in this bounded audit.

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
mode is. All ten locomotion modes now qualify, including mounted movement through
the creature-backed adapter. The movement row is therefore `verified`; the
combat row is also `verified` after #397.

`rules/vehicle_coverage.validate_coverage` rejects three drifts: an audit that
declares different modes from `VEHICLE_OPERATIONS`, a mode that claims a concern
it carries no operation for, and a residual whose owner is this audit itself or
one of the closed issues it supersedes. It also rejects a declared capability
status or owner that disagrees with the audit, so raising a mode is the only way
to raise a row.

Every bound #346 vehicle skill still records `gurps.vehicles.movement` as its
historical activation dependency. Because the row is verified,
`rules/mundane_skills/technology.unsupported_scope` no longer publishes those
procedures as blocked. Repairing a machine is not operating one, so the #356
Mechanic rows carry no activation blocker.

Evidence is in `tests/test_vehicle_coverage.py`.
