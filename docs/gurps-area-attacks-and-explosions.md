# GURPS area attacks, scatter, and explosions

Issue #512 completes the executable Campaigns fourth-printing B413-B415 path.
It extends the existing ranged, thrown-item, cover, object-damage, and injury
reducers; it does not introduce a parallel damage or inventory subsystem.

## Area declaration and scatter

`take_combat_turn.area_aim_point` declares a point in the encounter geometry,
independently of the command's participant target. The participant remains the
turn/visibility routing subject, but cannot defend against the attack roll. The
engine validates the point, geometry, payload, range, shot count, and incompatible
body-part/object/penetration selectors before drawing dice. Attacking an area gets
the B414 +4 modifier.

On a miss, the durable blast record contains the three attack dice, declared range,
one direction die, and the final scatter distance. Normal scatter uses margin of
failure and `scatter_squared` uses its square for the B414 flying, underwater,
Artillery, or Dropping exception; both cap distance at half range, rounded up. Six
clockwise directions have explicit axial-hex and square-grid adapters. A derived
scatter center may lie beyond the authored map edge, while all affected targets
still come only from authoritative encounter placements.

## Explosion settlement

`resolve_weapon_explosion` derives every living and durable-object target from the
recorded center and map distance. It requires actor responses and object cover for
exactly that derived set before its first damage, defense, fragment, or location
roll. Blast, fragment, cover, object, and injury packets continue through the
existing reducers in one compare-and-swap transaction. A thrown explosive is one
physical item: launch expends it once and blast settlement permanently retires that
same instance once, including after area scatter.

The reducer distinguishes direct, collateral, contact, and internal explosions.
Contact explosions maximize damage to the actor occupying the center and give
others that actor's torso DR plus HP as cover. Internal explosions require the actor
at the center, ignore DR, and apply the vitals-equivalent x3 injury. Air, water, and
vacuum retain their B415 collateral divisors; an armor divisor applies only to a
direct strike, never collateral damage or fragmentation.

Replay reconstructs blast centers, scatter evidence, object results, wounds, and
source retirement from stored command entropy without drawing new dice.
