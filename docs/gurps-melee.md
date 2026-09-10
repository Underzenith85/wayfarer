# Profile-selected melee and injury

Issues #103 and #102 use the existing CombatService, encounter state, canonical
resource pools, command receipts and atomic commit_turn boundary. No new HTTP
operation or alternate combat engine is introduced. Prototype campaigns keep
their original reaction budget, damage and rules digest.

GURPS combat requires an exact `CombatRules.gurps_equipment` catalog matching the
compiler profile, pinned equipment/skill definitions and inventory specifications.
Portable scenarios persist it as `combat_equipment`; profile activation remains
subject to the registry's certification gates. An existing checkpoint without
explicit injury/fatigue pools requires migration, not implicit conversion.

## Executed baseline

- A melee intent supplies an owned ready item, target and exact mode ID, never
  skill level, ST, damage, DR or dice. Trained or legal default weapon skill is
  compiled server-side. Reach and one-/two-handed occupancy are checked.
- Dodge uses Basic Speed-derived Dodge, load, injury and fatigue. Parry and Block
  use applicable skills and ready equipment, including shield defense bonus.
  Block is once per turn; Lite parries are once per weapon per turn; Basic
  repeated parries use cumulative penalties, reduced for fencing weapons.
- Basic weapon parries use B376's Basic Lift limit: one-handed modes cannot
  parry more than BL; two-handed modes can parry up to twice BL. Impossible
  parries are not offered, and forged choices are rejected before dice.
  Three times the parrying weapon's weight introduces a breakage roll instead
  of prohibiting the parry. The base 2-in-6 chance increases by one per whole
  additional weight multiple; cheap/fine/very-fine quality adjusts it +2/-1/-2.
  Breakage still stops the attack at up to 6-in-6; above that it does not, and
  All-Out Defense may try its second defense. Only successful contacts roll.
  Breakage preserves custody and synchronizes readiness, hand bindings and
  supported residual modes in the same CAS. Exact weights, quality, dice and
  the stopped/broken outcomes persist in an internal `heavy-parry-v1` event.
  Lite, ranged modes and spell attacks do not acquire this weapon-weight rule.
  Risky parries require explicit durability and `parry_quality`; an omitted
  value is not inferred from B556's coarser `critical_breakage` classification.
- Injury includes penetration, torso wounding factors, signed HP, major wounds,
  shock, stun/knockdown, consciousness and death thresholds. Held weapons/shields
  drop without unequipping armor. Stun recovery runs after forced Do Nothing.
- Low FP reduces Move/Dodge and effective ST for minimum-ST penalties, not base
  weapon damage. Continued physical activity at nonpositive FP checks compiled
  Will; a failure is durably committed even though the attack does not occur.
  Activity interrupts pending rest/medical work; Do Nothing does not.
- Lite critical attacks bypass defense; natural 3/4 use maximum damage. Basic
  torso critical hits execute the numeric B556 table, including extra damage,
  reduced DR, forced major wounds, double shock and dropped held equipment.
- Basic critical misses execute unready/drop, balance penalties and falling.
  Quality-driven breakage and flying-weapon collisions use the existing object
  reducers. Missing required equipment/anatomy bindings preserve the table roll
  and block with `adjudication_required` and `blocked_reason`.
  The blocked event also preserves immutable weapon modes and damage, build
  revision, equipment digest, HT, position/facing, limb DR, held items and any
  deferred incoming attack. Retrying or restarting reads the same record; it
  cannot replace the original table roll or recalculate context from later gear.
  Ordinary failed-parry consequences still allow the incoming attack to hit.
- With explicit human anatomy and canonical hand bindings, critical self-wounds
  execute through the location injury reducer, including DR, half damage,
  crippling, dropped grips and lasting-injury state. Impaling/piercing self-wound
  exceptions record exactly one additional table roll. Shoulder strain disables
  the wielding arm for 30 minutes while retaining the weapon, and cancels any
  remaining attack that requires that arm. The existing `parry_mode_id` and
  `second_parry_mode_id` choices now select armed melee Parry values and critical
  self-wound damage before dice. Unspecified ambiguous modes stay blocked;
  invalid modes or modes attached to a non-Parry defense are rejected before
  exertion or random checks. Blocked contexts retain the chosen mode, including
  All-Out Defense's second Parry. Residual weapons use their effective definition.

## Evidence and remaining blockers

`tests/test_gurps_melee.py` contains independently entered expected values for
trained/default skill, defense distinctions/repetition, missing heavy-parry
metadata, negative HP, critical damage, fatigue reductions and failed exertion, stun recovery and consciousness.
It covers unauthorized/forged requests, maximum-length command IDs, deferred
defense across SQLite restart, original-result replay after later turns, and
event replay. Existing PostgreSQL combat tests run when its test URL is set.

Source targets: Lite August 2004 revision 07/12/04, pp. 24-30; Basic Set first
printing with the declared January 26, 2007 errata, B369-376, B378-382 and B556.
Numeric comparison used the Campaigns fourth-printing table where available;
the first-printing/errata delta is not certified. No profile is promoted to
verified by these engineering tests.

B376 was inspected in Campaigns fourth printing (2008), including the
heavy-weapon box. `tests/test_heavy_parry.py` independently records weight and
quality boundaries, BL/2xBL equality, failed contacts, double defense, two actor
identities, duplicate-command concurrency and SQLite restart. The former
secondary-source 3:1 prohibition is superseded. `tests/test_melee_parry_modes.py`
checks selected-mode Parry values, critical self-wound damage, deferred context,
invalid-choice rejection and pending-defense restart/replay. The first-printing
and declared-errata comparison remains a separate #191 certification gate.

Coverage remains **partial**. [#146](https://github.com/Underzenith85/wayfarer/issues/146)
tracks the remaining Basic critical consequences and their dependencies on
#104/#107/#114. Complete maneuvers, initiative/timing and tactical defense options
remain #104; unarmed/grappling #108; ranged attacks #106. The heavy-parry path
does not add deliberately futile over-BL attempts and their
drop/knockback consequences, improvised weapon destruction, or effective weights
for unarmed attacks; those remain visible integration gaps under #103/#108/#114.
Advantage-specific defense exceptions remain unavailable and belong to #113.
The full GURPS profile remains unavailable until those capability gates pass.

The durable critical context is an internal handoff, not a GM override or a
client-supplied damage command. Breakage needs canonical quality/destruction, and
missing anatomy or wielding bindings preserve the limb blocker. Migrated blocked
consequences still require the explicit continuation work tracked in #290. No
generic retry may reroll a recorded miss. All-Out Defense's second critical parry also captures its own
weapon and deferred incoming attack; unsupported head-hit effects remain on
their separate head-table blocker.
