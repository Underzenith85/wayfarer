# Melee attack posture table

The selected Campaigns fourth printing B551 gives melee attack modifiers of
standing 0, crouching/kneeling/sitting −2, and crawling/lying down −4. Its
footnote permits only Reach C melee attacks while crawling. The table explicitly
leaves ranged attacks unaffected. B370 separately applies the usual posture
penalty to the attacker's takedown contest; this does not turn the table into a
general skill or attribute penalty. In particular, Feint skill contests retain
their ordinary skill targets.

The pure `posture_melee_modifier` and `posture_melee_reach_allowed` tables express
these rules. Weapon and unarmed attack target builders consume the posture
policy captured by their initiating attack, independently of the later
defender's generation. Admission checks require actual close distance for a
crawling attack: a weapon offering both Reach C and Reach 1 cannot attack from
Reach 1 simply because its mode also lists C. Existing movement, kick, grappling
and body-geometry restrictions remain applicable.

The private `melee-attack-postures` generation and producer-pinned attack policy
preserve historical scoring when an attack was declared without the rule.
Changing fresh feature settings before its defender responds must not change
that attack's semantics. The policy is private engine state rather than a
player-selected modifier or a public command field.

Only rule policy is pinned at declaration. Current attacker conditions and
posture still feed the ordinary target calculation before its first roll.
Once an original attack roll has been captured for the existing Luck/opponent
choice, that roll retains its immutable context. Direct and typed
`BeginOpponentAttack`/`ChooseOpponentAttack` cases reexecute both the producer
and response from their recorded seeds after fresh feature settings change.

Actual Change Posture commands and the existing free crouch producer supply
the poses in the source cases. Actual punches establish shared close combat
before lowering posture, with no invented grip, position or geometry. Takedown
cases lower posture before acquiring their actual grapple, respecting the
current prohibition on changing posture during a grapple. Source cases cover
weapon injury, successful and failed close punches, Reach C refusal before
dice, takedown consequences, Feint exemption, exact retries and seeded replay.

The current approved composed Innate Attack carrier offers beam, projectile,
breath and gaze specialties with ranged delivery. It has no canonical melee
reach producer, so this correction does not invent one or apply melee posture
penalties to those ranged attacks. Held melee spells delivered by actual
ordinary weapon or punch attacks use those attack builders; unsupported bare
Touch variants do not acquire a new authoring path here.

Evidence: `tests/test_melee_posture_table.py`, `tests/test_melee_postures.py`,
`tests/test_melee_posture_replay.py` and `tests/test_melee_posture_spell_contact.py`.
These bounded source checks do not promote
whole-source certification or claim completed posture footprints, ranged target
concealment or flying/swimming Dodge and Drop geometry.
