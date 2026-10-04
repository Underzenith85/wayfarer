# Deathtouch through a selected hand

The first hand delivery consumer is one ordinary punch with the exact empty,
usable hand that holds a paid Deathtouch charge. Characters B240 requires an
ordinary DX or unarmed attack; spell skill does not replace the punch check.
The physical blow retains its normal armor, location, critical and training
rules. Its magical packet remains separate source energy dice and ignores
armor. Treating physical critical maximum/double/triple as modifiers to the blow,
while retaining the spell's selected energy dice, is the composed B240/B556
interpretation used by this consumer.

A successful Dodge or weapon Parry retains the charge. A successful barehand
Parry prevents the punch but permits the armor-ignoring magical arc. These
outcomes use the actual rolled defense and implement identity, rather than
equating every failed punch with a successful parry. Passive parrying with the
caster's charged hand does not discharge it. Missing, critical missing and
weapon counterdamage preserve their canonical physical consequences.

Current source and victim admission precede attack and defense dice. Deferred
physical damage revalidates immediately before its dice; direct resolution can
carry an ephemeral same-call proof. No trusted validation flag is serialized.
The caster's own armor-caused hand injury during a lawful punch does not
retroactively undo its accepted contact. Combined injury refresh updates actual
posture, readiness, incapacity and concentration before the existing maneuver
settlement, without a fabricated second wound or an extra turn advance.

Contextual B557 pauses retain the new private contact, charge and actual pending
attack. The engine blocks further defense selection; an unsupported contextual
continuation cannot buy a fresh attack roll. Ordinary uncharged historical
pending behavior remains unchanged. A determinate barehand magical arc can
settle even if the ordinary critical consequence pauses. An unresolved failed
critical defense must not masquerade as a completed miss or an immutable
no-effect receipt.

First scope excludes Block, joint-grip wrench, kicks, touch aliases, multiple
attacks/defenses and attack-selection Luck. The ordinary typed physical damage
and weapon-counterdamage stages retain their actual pending association.
Historical Staff casting and ordinary uncharged attacks retain their existing
payloads; hand casting and contact use isolated private generation records.

`tests/test_hand_deathtouch_contact.py` exercises real approved hand casting
(skill16, selected energy3, actual payment2FP), then the actual combat service
and typed damage-task routes. Named cases prove hit/failure/miss, Dodge retention,
barehand magical arc, canonical close-range knife parry counterinjury, separate
critical triple/double/maximum packets, and rollback when magical randomness
fails after physical damage. Exact retries and fresh service reconstruction
preserve the accepted result and state without another magical packet.

The contextual-pause cases distinguish a determinate missed attack, a successful
barehand arc, and an unresolved failed critical parry. Their fresh-command
refusal uses a random source that raises on every attempted draw, rather than an
empty replay source whose exhaustion could hide a reroll. Captured pending
attacks remain blocked; ordinary migration remains available, and this suite
does not claim a qualified contextual continuation exists.

The typed-stage case proves real `PrepareOwnerDamage`/`ChooseOwnerDamage(accept)`
settlement: strike damage discharges only at final injury, while weapon-parry
counterdamage retains the charge and immutable held result throughout its owner
stage. Attack selection and double defense remain rejected before randomness.
The pure hand helper and reducer diagnostics alone are not acceptance proof.
Full original-genesis seeded replay, projection and generation-absence coverage
are separate integration obligations; this file does not claim whole Melee
spell or issue acceptance.
