# Thrown items and catches

Basic Set thrown attacks retain the same item instance in durable expended inventory.
A hit records the target's position; an ordinary miss or deflection leaves landing
unresolved. The GM can use `declare_thrown_landing` once to specify a position on the
original battlefield. Witnesses receive tactical v2 equipment status; other actors
receive no item projection. Non-owners do not receive private condition details.

`recover_thrown_item` requires a dedicated Ready, the recorded position, kneeling,
sitting or prone posture, and an explicitly selected free usable hand. Existing
inventory capacity and hand binding validation still apply. Broken weapons cannot
be readied by recovery. Items above Basic Lift remain rejected: multi-second heavy
lifting and elongated-arm reach are outside this one-Ready pickup procedure.
Interrupted or aborted Ready work does not move an item out of expended inventory.

`RangedMode.catchable` explicitly enables barehanded Parry for a one-handed thrown
weapon in the exact Basic Set profile. The B376 weight penalty applies. Choosing
`catch_thrown` catches only on a critical successful barehanded Parry (B381), never
on ordinary success. Catching transfers ownership and binds the selected hand;
occupied, grappled or crippled hands remain unavailable. Critical failures use the
unarmed consequences, not weapon breakage on a hand identifier.

Commands use the normal receipt/CAS transaction. Retrying a resolved attack,
landing declaration or pickup never creates another copy. The fixtures cite
Campaigns fourth printing B376/B381/B383; first-printing/errata reconciliation and
overall catalog certification remain pending under #191.
