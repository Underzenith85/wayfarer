# Basic Set vehicle operation audit (#207)

This adapter extends the #208 transport foundation inside the existing resource
transaction. It does not supply the live mounted/vehicle encounter integration
originally assigned to #120. Full vehicle movement/combat capabilities remain
**partial**, and #207 is not acceptance-complete.

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

Source: GURPS Basic Set, Campaigns Fourth Edition, fourth printing, B394-395,
B430-432 and B468-470. This does not supersede the repository's frozen first
printing plus 2007-01-26 errata baseline. Cross-printing/errata reconciliation
remains part of #191; this PR does not certify that reconciliation.

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

## Residual coverage blockers

These are remaining #207 work, not completed merely by having a pending state:

| Missing consumer or variant | Current behavior / owner |
| --- | --- |
| Space thrust, navigation, fuel/delta-v and very large speed/damage scales | Navigation rejects; only control/stress and resolved collision exchange are provided. #207 |
| Vertical flight, climbing/diving trajectories, continuing stalls/falls and terrain-relative air-crash consequences | Altitude/control facts persist, but full three-dimensional movement and ongoing descent are not implemented. #207 |
| Sinking, capsizing recovery, underwater stress damage, leaks, decompression and open-deck overboard checks | Pending states block ordinary operation; open-deck control rejects before dice. Integrate with environmental consumers and #181. #207 |
| Ejection destination, subsequent impacts, swimming/rescue and removed occupant manifests | Ejection distance is computed, but placement and follow-on hazards remain pending. #207/#120 |
| Slopes, terrain-specific travel tables, airborne drift, water currents, fractional draft and unsafe terrain deceleration beyond the supported envelope | Reject; no inferred travel behavior. #207 |
| Minor skid paths through difficult terrain or other actors; fractional endpoint reconciliation | Reject the ambiguous path; exact residual thirds are saved. #207/#105 |
| Vehicle hit locations, operator incapacitation, ongoing stress below zero HP, disabled equipment effects, and actual leak/engine-failure consequences | Existing object damage is reused, but these live consumers remain under #181/#207. |
| Ramming attack/defense, mounted weapons, cover, Aim and penalty consumption, and synchronized encounter poses | Live integration originally assigned to #120 remains missing despite that issue's closure. |

No unsupported outcome is replaced with an LLM ruling, default damage, random
invented malfunction or a full-coverage claim. #105/#107 and #181 remain dependency
owners for geometry, injury and live durability integration. The bounded tests in
this PR establish only the operations listed above.
