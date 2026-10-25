# Corrosion and physical armor (B379)

The uploaded selected *Campaigns* fourth printing requires corrosion to destroy
one DR per full five points of basic damage. The current hit uses its original
protection; the loss applies to future hits. Armor is affected before natural DR.
A stopped hit can therefore erode armor without causing HP injury, and separate
sub-five hits do not accumulate into an erosion point.

The bounded shared implementation supports a real approved Corrosion Innate
Attack source, with no modifiers or only the reviewed Cyclic enhancement. The
target may be unarmored or wear exactly one quantity-one, unsplit, torso-only
mundane armor item, with no natural or active DR. Enchanted or condition-replaced
profiles, front-only protection, multiple items,
other locations, armor divisors, damage at or beyond half-damage range, other
modifiers, and unreviewed protection compositions refuse. Target protection is
checked before declaration and again at impact. These restrictions are
explicit runtime boundaries, not claims that the omitted rules are optional.

Newly bound sources implement every native B556 torso critical-hit row, without
an active-defense roll: triple/double damage, maximum normal damage, half DR
rounded down, forced major wound, double shock, normal damage and unconditional
dropping of actual held items. Critical multipliers and maximum damage change
the actual basic packet used for erosion. A private captured body-table descriptor
authenticates critical flags separately from full current physical armor DR; the
reducer halves only effective protection for this hit, then erodes the original
item from the actual packet. Maximum damage consumes no random damage dice.

Critical misses still capture the original attack and actual B556 table dice,
including the one required miss-table reroll of an initial 5 or 6. The existing
durable pending attack remains blocked with no damage or erosion applied. Exact
retries return that original; a new response cannot reroll it. No owner-damage
Luck opportunity is opened for an unresolved miss. The existing seated-GM
continuation may apply an explicitly named campaign policy, such as a timed source
disable; that is GM adjudication, not a claim that the printed miss effect is
implemented. Head-table ignore-DR effects and self-hit corrosion remain separate
unsupported rules.

A private source generation identifies the new erosion contract. Existing
unmarked low-level corrosion receipts and unbound cycles retain their previous
HP-only behavior. Before this change, the actual composed source producer refused
all corrosion bindings. No historically valid composed source is silently given
a new meaning. The independent private `corrosion_critical_generation` on binding
commands captures the new native-hit contract: absent metadata retains older
sources’ native rows 9–11 and durable pauses for other critical hits. Their
recorded attack/table chronology and explicit GM policies remain replayable.
The source generation also persists with a delivered Cyclic
binding; each future deadline reopens current target protection and records any
new erosion through the same canonical item ledger. Living targets use the
canonical injury reducer. A dead wearer retains HP immunity, while each later
packet still rolls against its authenticated remaining worn armor and records
only DR loss. It creates no HP/FP injury, recovery debt, or wound receipt; Cyclic
counters and deadlines advance normally. Once no armor DR remains, ordinary
dead-body damage-draw suppression resumes.

The reducer receives authenticated current armor identity separately from the
public wound command. It scores HP injury, then records immutable erosion against
the physical item UUID and pinned equipment profile digest. The erosion records
the actual basic damage, previous loss and saturated total. It does not change
object HP, mutate catalog rows, or invent a damaged-equipment variant. A shared
item-aware projection supplies reduced protection to ordinary melee, ranged,
unarmed and thrown attacks, critical contexts, area damage, physical collision
checks and future Cyclic ticks.

Loss follows the same physical item through unequipping, transfer and re-equipping.
An identical replacement item starts with its own protection. Time does not heal
armor erosion, and changing object HP does not reset it. Existing B484 repairs
restore object HP; they do not acquire a DR-restoration effect by inference.
Changed pinned profiles refuse instead of silently discarding prior erosion.
Source-specific armor-DR repair or replacement procedures remain unimplemented.

Tests use the actual B283 Lorica Segmentata profile, DR 5 covering the torso.
A real approved, bound and delivered five-point corrosive hit is stopped, leaves
HP unchanged and reduces later protection to DR 4. An ordinary sword attack then
causes the source-derived extra injury. Separate tests drive actual Cyclic
production and successive deadlines, including actual sword-inflicted death
while armor remains followed by packet erosion without further HP or debt. They
verify saturation and sub-five boundaries,
reject unsupported compositions atomically, execute all critical-hit rows on
both direct and owner-damage routes, retain and reexecute historical critical-hit
pauses and current critical-miss pauses with explicit GM policy, preserve
item identity through actual inventory reducers, and reexecute complete source,
attack, damage, subsequent sword and Cyclic commands from genesis on SQLite and
PostgreSQL. A separate restored canonical checkpoint drives actual B484 repair
commands to restore armor HP from 2 to 12 while its DR remains 5 instead of 6;
that repair consumer also reexecutes on both stores. These tests do not certify natural-DR erosion/healing, layered or
location-wide allocation, specialized penetration, generic corrosive weapons,
critical-miss native continuations, or every Innate Attack/Cyclic modifier.
