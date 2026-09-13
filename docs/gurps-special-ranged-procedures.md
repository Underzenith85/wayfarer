# Special ranged procedures

Issue #511 was checked against the supplied Campaigns fourth printing, pages
B407-B413. The implementation reuses the existing object-damage, firearm,
rapid-fire, ammunition, aim, scope, laser, thrown-weapon, and explosion owners.
It does not add another weapon-family resolver or infer support from a name or
technology level.

## Cover and overpenetration

An object can act as cover only when its durability profile explicitly marks it
as structural cover or a thin barrier. Structural cover combines DR with one
quarter of maximum HP before applying the attack's armor divisor. A thin slab
uses DR alone. Living cover uses HP plus worn armor on both sides. Only piercing,
impaling, and tight-beam burning ranged damage may continue through cover.

`impact_cover` identifies a projectile, barrier, and intended target together.
It sends the original basic damage through the existing object reducer exactly
once, records a cover-impact event, and returns the remaining damage budget.
Retrying the same projectile/barrier transaction returns the recorded result;
it cannot damage the barrier a second time or create a second projectile.

Geometry remains authoritative at the caller boundary: mapped combat must select
an intervening ground object already present in the encounter. Worn equipment is
armor and cannot be submitted again as intervening cover. Unsupported damage
types and objects without a cover adapter reject explicitly.

## Guided and homing attacks

`RangedMode.guidance` is the equipment-side availability gate. It distinguishes
operator-guided, self-homing, and semi-active homing projectiles. Homing adapters
must name their seeker sense; guided weapons use the operator's senses; semi-active
weapons additionally require a recorded designator. Guided adapters are kept out
of the existing special rapid-fire family.

Successful acquisition creates a `GuidanceState` in the encounter. The mode's
1/2D value is interpreted as speed rather than a damage falloff boundary, while
Max is the total flight endurance (B412-B413). Each one-second advance records
distance and endurance. Operator interruption or loss of sight ends guided
flight; seeker jamming ends homing flight; loss of designation ends semi-active
flight. Arrival and loss are terminal. The complete state round-trips through
the encounter snapshot, so replay never reacquires a lock or redraws dice.

## Boundaries retained from adjacent owners

The B407 malfunction dispatch and its low-TL, grenade, single-use, and beam
variants remain in the firearm transition and explosion modules. B408-B410
automatic-only, shotgun, spraying, and suppression paths remain in their existing
rapid-fire handlers. B410-B412 special thrown weapons and firearm accessories
remain available only where both the audited equipment row and its runtime
adapter exist. These procedures do not broaden those families.
