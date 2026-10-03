# Haste and zero-cost Power wearer effects

This bounded #797/#785 implementation was checked against the supplied Basic Set
Characters third printing B237–239/B251 and Campaigns fourth printing B480–482.
Neither issue is complete. Source PDFs and source prose are not included.

Haste has IQ/Hard construction with no named spell prerequisites. The private
Haste host executes one to three levels through the shared spell reducer. Each
level costs two energy to cast and one to maintain. For a positive Size Modifier,
B239 multiplies both by 1 + SM before high-skill or item Power reductions; negative
SM gives no discount. Cast costs and paid-item upkeep origins retain the accepted
size at activation; magnitude still describes only the Move/Dodge bonus. Listed
casting time is two seconds and duration is one minute. Personal high-skill benefits, interruption,
ordinary failure, cancellation, the shared clock and recorded randomness remain
owned by that reducer. Item casts retain listed time and energy before Power.
The subject must be an approved actor. Item Haste channels are self-only.

Active Haste changes actual walking Move and Dodge. It does not change Basic
Speed or initiative. The strongest active instance applies (B237); instances do
not add together. The bonus is applied to encumbered Move/Dodge before the
existing low-HP/FP reductions. Existing overload and disabled-leg restrictions
still prevent their respective actions. Combat movement consumes the current
allowance; unrolled defenses consume the current bonus. A committed defense and
its exact retry retain their original check after the item is switched off.

## Explicit items and current conditions

A private director declaration binds a completed Haste enchantment to its real
item definition, clothing/jewelry form and one-to-three-level magnitude. The
magnitude is never decoded from a project, recipe or effect identifier. Hand-slot
weapons cannot stand in for Haste's printed clothing/jewelry forms. This host
activates already completed items; it does not extend the frozen enchanting
recipe enum or claim Haste item manufacture is complete.

Current trusted director observations give the mana level of each applicable
world location. Missing location evidence leaves the zero-cost wearer effect
inactive, and refuses a paid item activation. Personal Haste uses its separately
declared channel. No environmental mana is inferred from a location name.

When surviving Power reduces the selected item's casting cost to zero, equipping
it in its normal wearable slot immediately puts Haste on its current wearer.
There is no casting roll, casting time, concentration, FP payment or upkeep
payment. Sleep and unconsciousness do not end this zero-casting-cost effect.
The owner can switch it off and on while awake. The switch is an item setting and
persists across transfer. A stowed, contained, dropped, merely readied or unequipped
item cannot provide the worn benefit. A new holder receives it only after
actually equipping it. Every checkpoint rechecks current approval, item-wide
Magery restrictions, custody, the item's surviving enchantments and current mana.

The item owns the automatic spell effect; the wearer is its subject. Therefore
it does not invent a personal cast or a personal spells-on penalty. The ordinary
spell projection owns the effect and later Move/Dodge consumers. No second
clock, movement total, energy ledger or combat roll is added. No-mana or inadequate
Power suppresses the effect; eligible reentry creates a new effect. Physical
breakage uses the existing permanent magic-loss records, so ordinary repair does
not bring it back. A fresh completed enchantment with a new binding ID can receive
a new declaration on the repaired item. Historical magnitudes and switches remain
immutable; surviving or previously lost binding IDs cannot be redeclared.

A paid cast whose upkeep alone is reduced to zero remains the separate B480
case. It retains the original actor/subject, paid interval, charge and canonical
Power origin. An awake wearer renews it without cost. Sleep or unconsciousness
prevents the next renewal; it does not erase the already paid interval. A later
wake does not resurrect the ended cast. The existing outward zero-cost refusal
remains in place for unsupported spells.

## Persistence and scope

`HasteService` and its private declarations enter the existing command pipeline.
Current membership and actor control or the seated trusted director authorize
commands and exact retries. CAS, deterministic entropy, recorded command input,
restart, event folding and seeded reexecution use the existing stores. New event
prefixes reject execution records at authored genesis. Haste is added only to
`RuntimeSpellId`; public spell commands, spell effects, authored scenarios,
responses and their reviewed schemas remain closed. Old commands with no Haste
records acquire no new events or bonuses.

`test_haste_effects.py` checks source cost, time, magnitude and expiry, and an
approved personal Haste3 cast changing real Move5 to8 and Dodge8 to11.
`test_power_wearer_haste.py` exercises the actual wearable item, current mana,
off/on, custody transfer, breakage/repair, approval/Magery, sleep, the paid-upkeep
contrast, low-HP/FP and disabled-leg restrictions, real combat movement and Dodge,
exact concurrent retries, restart and seeded reexecution. The existing paid Power,
item lifecycle and private spell-vocabulary regressions remain applicable.

Open boundaries remain explicit: optional high-Magery levels above three; charged always-on compositions; travel
into locations without authored mana evidence; spell-specific backfire reversal,
Counterspell/Dispel Magic; manufacture through a private Haste recipe; and other
movement modes beyond the existing walking consumer. Apportation and Great Haste
have merged baseline consumers; see `gurps-haste-construction-acceptance.md` for
the written #797 case map and [the remaining Movement host carriers (#980)](https://github.com/Underzenith85/wayfarer/issues/980) for additional Movement carriers.
Other wearer spell families, outward zero-cost interpretations,
duplicate Power composition and the remaining enchanting contracts remain under
#785. This is not whole-college or whole-issue certification.
