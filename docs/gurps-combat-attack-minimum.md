# Voluntary combat attack minimum

Campaigns, fourth printing, B345 permits a success-roll attempt only at effective
skill 3 or higher, with active defenses as an explicit exception. A roll of 3 or
4 being a critical success does not authorize an attack at effective skill 2.
Fresh exact-Basic-Set ordinary weapon attacks and unarmed punch, kick, grapple and arm-lock
attempts now check the exact canonical fully modified attack target before their
initial maneuver, fatigue payment or random draw. For example, Broadsword13,
darkness−6 and neck−5 refuses without rolling or spending the maneuver; the same
attack with darkness−5 has target3 and remains lawful.

Admission reuses the existing melee, ranged and unarmed `AttackRollSpec` builders;
it does not maintain another target formula or globally forbid low-target dice.
The no-dice preparation projects the actual declared maneuver and movement and
already-earned very-high-mana refunds. Successful start-of-turn consciousness
and exertion checks do not change the attack target: shock expires at turn end,
not on a successful start check, and successful exertion does not spend FP.
Failed checks retain their existing durable unavailable/Do Nothing result.
An already-earned refund is different: restoring FP can restore usable ST and
remove an insufficient-ST weapon penalty, so preparation includes that refund
before deciding whether the attack is lawful. No simulated random health outcome
is drawn or discarded. Tactical previews use the same preparation without dice,
including random bystander selection; actual declarations retain their native
selection and old recorded commands retain their original entropy order.

A private producer record pins only the minimum and B551 melee-posture rule
policies. It never freezes a numerical target at declaration. A defender response
refreshes the current sensory and condition state and rechecks the canonical
attack target before Acrobatic Dodge or any other response-option dice. A real
Tipsy condition can therefore change an unrolled target3 into target2. Refusal
preserves the unrolled pending attack; the attacker can use the existing
`AbandonPendingAttack` continuation, which preserves spent resources and the
maneuver's restrictions and performs ordinary end-of-turn settlement once.
Within an accepted response transaction, options and final resolution share the
same pre-reaction attack specification.

The original roll captured by the existing typed opponent/Luck pipeline retains
its immutable context. That pipeline already prevents unrelated commands from
advancing play while the immediate roll choice is pending. Historical producers
without the new minimum policy retain their old behavior even when a later
response uses fresh rules; recorded seed reexecution preserves that distinction.
The shared raw `AttackRollSpec.roll` remains usable by mandatory fragmentation,
stray fire and other involuntary consequences, and active-defense rules are
unchanged. Shield rush, suppression, purchased composed attacks and magical
missiles retain their existing specialized admission paths and are outside this
bounded implementation; this does not certify their complete B345 coverage.

`tests/test_combat_attack_minimum.py` checks actual weapon and unarmed targets2/3,
ranged ammunition preservation, both-store seeded execution/retry/replay,
source-produced current alcohol changes and abandonment, health-check ordering,
actual very-high-mana spell refunds and typed original-roll continuation.
B551 posture and producer-policy tests live in `test_melee_posture*.py`.

The producer and consumer policy gates require the exact Basic Set equipment
profile. Lite and prototype combat retain their native targets, posture behavior
and dice, and neither captures these Basic Set attack policies.
