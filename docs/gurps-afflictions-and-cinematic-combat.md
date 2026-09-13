# GURPS afflictions, penetration, and cinematic combat

Issue #513 covers the engine boundary selected from *Campaigns* B416-B417 and
the attack-modifier definitions on *Characters* B102-B116. The implementation
uses authored, typed inputs and keeps attack, resistance, scheduling, and
resource mutation replay-safe.

## Afflictions

An Affliction channel declares its approved condition, duration, attack roll,
resistance roll, and penetration context. A successful hit applies the target's
DR as a resistance bonus unless the authored delivery changes that rule. A
failed resistance roll creates one typed `AfflictionEffect`, one active effect
ID, and one scheduled expiry. Replaying the same authoritative command returns
the prior state and outcome without creating another effect or expiry.

The approved Affliction purchase constrains the closed condition type to its
purchased family: stunning, attribute penalty, or incapacitation. Homogeneous
Injury Tolerance rejects choking before condition state is written. The hazard
catalog and duration variants on B428 are outside this issue and remain
separately owned.

## Special penetration

The pure penetration resolver separates three values that must not be conflated:

- whether the delivery reaches the target;
- DR added to the resistance target after an armor divisor; and
- a resistance modifier derived from penetrating injury for Side Effect.

It implements ordinary and divided DR, Linked and Follow-Up delivery, and the
Blood Agent, Contact Agent, Respiratory Agent, and Sense-Based routes described
on B416 and B102-B116. Route facts such as exposed skin, an open wound, breathing
protection, an available sense, and carrier penetration are trusted authored
facts rather than inferred from free text. Wounding remains in the existing
injury resolver; the penetration resolver never applies a damage-type wounding
multiplier.

## Cinematic combat selection

Dual-Weapon Attack is enabled only by the exact profile rule ID
`gurps.techniques.dual-weapon-attack`. Supplying a second Attack weapon while
that rule is disabled is rejected during command validation, before dice are
drawn. When enabled, the engine requires two distinct ready one-handed weapons
bound to different hands, records each hand's penalty, rolls and defends against
each attack separately, retains each weapon's ready state, limits two melee
targets to adjacent combatants, and applies the divided-attention defense
penalty when both attacks target one opponent (B417).

The existing Whirlwind Attack technique remains separately gated by
`gurps.techniques.whirlwind-attack`. Other adjacent B417 cinematic variants are
not silently activated by this work: they remain unsupported and therefore have
no accepted runtime command path.

No prerelease engine version increment is required.
