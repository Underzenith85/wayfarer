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
  Rows requiring weapon quality/destruction or flying-weapon collisions persist their table roll and
  block the encounter with `adjudication_required` and `blocked_reason`.
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
  remaining attack that requires that arm. Parrying weapons with multiple damage
  modes remain blocked for self-wounds until a canonical mode is selected.

## Evidence and remaining blockers

`tests/test_gurps_melee.py` contains independently entered expected values for
trained/default skill, defense distinctions/repetition, negative HP, critical
damage, fatigue reductions and failed exertion, stun recovery and consciousness.
It covers unauthorized/forged requests, maximum-length command IDs, deferred
defense across SQLite restart, original-result replay after later turns, and
event replay. Existing PostgreSQL combat tests run when its test URL is set.

Source targets: Lite August 2004 revision 07/12/04, pp. 24-30; Basic Set first
printing with the declared January 26, 2007 errata, B369-376, B378-382 and B556.
Numeric comparison used the Campaigns fourth-printing table where available;
the first-printing/errata delta is not certified. No profile is promoted to
verified by these engineering tests.

Coverage remains **partial**. [#146](https://github.com/Underzenith85/wayfarer/issues/146)
tracks the remaining Basic critical consequences and their dependencies on
#104/#107/#114. Complete maneuvers, initiative/timing and tactical defense options
remain #104; unarmed/grappling #108; ranged attacks #106. Weapon breakage against
heavy parries and advantage-specific defense exceptions remain unavailable.
The full GURPS profile remains unavailable until those capability gates pass.

The durable critical context is an internal handoff, not a GM override or a
client-supplied damage command. Breakage needs canonical quality/destruction,
missing anatomy or wielding bindings preserve the limb blocker, and flying
weapons need authoritative collision handling. No generic retry may reroll a
recorded miss. All-Out Defense's second critical parry also captures its own
weapon and deferred incoming attack; unsupported head-hit effects remain on
their separate head-table blocker.
