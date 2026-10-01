# Push, knockback and Immovable Stance

Selected sources: Characters third printing B182/B201/B215/B216 and Campaigns
fourth printing B372/B378, with the existing B381-382/B556-557 critical consumers.
This is incremental #806 engine work, not a certification or API/UI claim.

## Push

The internal `declare_push`/`defend_push` reducer performs an ordinary barehanded
Attack against an explicitly selected living-human target. It requires a real
approved Push purchase and canonical Trained By A Master purchase. Weapon Master
alone is insufficient. The source imposes no concentration delay or per-attempt
FP cost for Push or Immovable Stance; the unrelated Power Blow/Breaking Blow
preparation penalties and 1-FP costs are not applied to them.

Push rolls its learned DX-based skill with existing shock, condition, posture,
darkness, Evaluate and Feint consequences. A successful attack pauses for the
actual defender's legal Dodge, barehanded Parry or close-reach weapon Parry.
Block is unavailable in close combat under B392. Entering the defender's hex
retains the recorded approach for B390-391 side penalties and rear-defense
prohibition; height still uses the actual post-entry placement.
It uses the higher of ST and learned Push for swing damage, including the
referenced one-hand shove reduction, then doubles the result for knockback only.
The attack does not wound the defender or transmit armor self-injury to the
attacker. Weapon parries and existing critical-miss consequences can still injure
the pushing limb. Critical Dodge falls;
critical hits suppress the defense roll and modify knockback damage as applicable.

Canonical continued exertion applies, including persistent collapse on a failed
zero-FP Will roll. Fatigue does not halve the attack's damage ST. The ordinary
Attack resets the prior maneuver's incompatible bonuses/defense prohibition.
Both build approvals, the exact encounter commitment and clock are bound across
the pause. Illegal declarations/defenses reject before random draws. Exact command
retries survive checkpoint reload without another roll or resource expenditure.

## Displacement and balance

Campaigns B378 drives crushing knockback: basic damage produces whole yards from
the resisting defender's current ST, with the source low-ST minimum. Cutting
knockback requires no penetration. An unconscious, collapsed or nonresisting
defender uses HP instead. The resisting defender's canonical fatigue projection
applies.

The shared consumer moves actual battlefield placement and checks the whole
occupied hex footprint. A Push that starts in close combat records facing as its
one-step displacement direction; a closing Push retains its pre-entry origin.
Balance uses the highest approved DX, Acrobatics or Judo, with the penalty for
yards after the first. Failed balance sets both combat and injury posture prone.
Perfect Balance contributes its purchased source bonus. Displacement clears stale
close-combat relationships.

B201 Immovable Stance requires its purchased skill and mastery, plus a conscious,
grounded, capable defender. It rolls against actual potential yards. Success
prevents displacement; failure permits ordinary balance; critical failure causes
full displacement and a fall without a second balance check. An elected reaction
is validated before damage dice, even if damage would ultimately be zero. Its
exertion roll occurs only if the rolled force requires a reaction; an actual
active-defense exertion check is reused rather than charged a second time.

## Explicit remaining scope

- Stance against Judo Throw, other fall-only attacks and broader action exposure
- Combined Power Blow/Push strength, timed Power Blow preparation, and Breaking
  Blow armor/brittleness/critical self-injury
- Push Wait interruptions, existing grapples, aerial/high-speed interactions,
  attack sequences and additional defense-option declarations
- Multi-hex close-combat entry and contact at non-anchor occupied hexes; already
  close contact at the target's anchor can displace a multi-hex target and checks
  its complete footprint
- Collision damage under B430, unknown mapless direction, and map-edge resolution

Unsupported declared combinations reject explicitly. Collision/map-edge and
mapless-direction outcomes retain the rolled facts and block the encounter for
adjudication without inventing movement or collision injury. Context-dependent
critical-table outcomes retain the existing explicit adjudication boundary.

`tests/test_push_combat.py`, `tests/test_push_geometry.py` and
`tests/test_cinematic_displacement.py` inspect real positions, postures, injury,
fatigue, legal defenses, purchased prerequisites, deterministic failures and
checkpoint/store replay. Mastery prerequisites reuse the canonical #767 behavior;
this change does not add unrelated mastery sequences or promote source ledgers.
