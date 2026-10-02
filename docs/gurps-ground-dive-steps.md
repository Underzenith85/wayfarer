# Ground Dive for Cover admission

Basic Set Campaigns fourth printing B377 resolves successful Dive for Cover
movement/cover before the explosion, and failed movement afterward. B368 bounds
a Step to ceil(current Move/10), permits passage through allies, and requires a
separate lawful consumer to pass an obstructing foe. Existing mapped movement
rules retain their terrain and elevation requirements.

The `ground-dive-step` captured private combat generation validates a route to
the existing authored BlastResponse destination before any explosion roll. It
uses current compiled Move, mapped blocked cells and occupancy. Hex admission
uses the canonical Step reducer, including elevation/stairway and footprint
checks. Square admission searches cardinal routes within the Step budget.
Ordinary destinations cannot share an actor's space; the existing authoritative
sacrificial-contact declaration permits final sharing only at its validated
blast center. This does not create an evade, climb or flying route.

Fresh direct combat commands capture the activated feature. Commands recorded without it keep
legacy admission; exact retry and server-seed reexecution select recorded
features. No public response schema changes. The four response validation joins
use the declared environment already captured in BlastProgress.

Water-declared and airborne actors retain legacy behavior. The two-dimensional
GroundPosition carrier supplies no swimming depth, aerial displacement or
selected medium-specific concealment. This slice does not establish those
consumers or complete issue #878.

Actual-host tests establish blocked destination/path and occupied destination
rejection before randomness, current Move 11 two-yard route admission, hex
height rejection, successful/failed HP and movement ordering, explicit contact
sharing, rollback/retry, authority/revision, both stores, and seeded reexecution
across changing active features. Existing explosion tests cover the ordinary
resolution and water/flying behavior. No injury reducer or damage ordering was
changed.

Direct ResolveWeaponExplosion and Task fragment response amendments, opening,
and choice use their captured private ground generation. The Task consumer and
its current-state, retry, and replay proofs are documented in
gurps-task-fragment-ground-dive.md. Recorded feature absence retains legacy
admission in both hosts.

Mapped ground admission also requires release/escape before translating a
retained grip, matching the canonical hex reducer. B371 describes automatic
release or sufficiently strong dragging as separate consequences of a holder's
movement; this bounded Dive does not implement those consequences. The real
host already refused a grappled or pinned diver in its outer response guard.
A new coherent grip-holder host regression demonstrates the square omission
before randomness and its corrected rejection. Legacy absence and excluded
water/airborne admission retain their prior behavior.
