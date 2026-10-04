# GURPS Basic Set enchanting projects

The project host from #526 is reconciled under #785 against selected Campaigns
fourth-printing B480-482. A recipe names
that cataloged spell, its target equipment definitions, explicit enchanting
method, energy, materials, workspace, activation, Power adjustments, charge or
maintenance shape, and an optional registered runtime family.

Quick and Dirty work takes one hour per 100 energy (rounded up), requires an
explicit contribution from every enchanter, applies the assistant penalty, and
spends the promised FP/HP only when the final roll is made. Slow and Sure work
contributes one energy per mage's eight-hour shift, with a separate daily schedule
and nightly rest. Completion, interruption and abandonment preserve each mage's
next permitted shift across new projects and items. Missed workdays require their
source-derived replacement shifts. The lead's lower approved Enchant/effect skill
sets the roll and Power; assistants must know both at 15+, or 20+ in low mana.
Quick and Dirty assistant and nearby-nonparticipant penalties, and extra
ceremonial energy for either method, affect the result and permanent Power,
while low mana is temporary. Natural 16 fails; 17-18 critically fail.

For new Slow and Sure work, the first BeginEnchanting command can commit extra
energy. It costs additional mage-days on the same daily calendar, not FP or HP.
The B238 thresholds are applied to the extra proportion of the recipe's original
cost: 20/40/60/100 percent grant +1/+2/+3/+4, then each additional full 100 percent
grants another +1. For example, a 30-energy Staff with 6 extra energy and two
mages takes 18 working days, ending at 17 calendar days plus eight hours. A
lead skill of 17 then yields a normal roll target and permanent Power of 18.
The choice is fixed before the first work begins; interrupted work retains it,
and changing the commitment after work starts is explicitly refused.

The frozen project field energy_completed represents base recipe progress and
continues to stop at the recipe cost. It does not decide completion: active_work's
deadline includes all promised extra energy and required makeup days. The
committed Begin receipt owns the extra goal; interruption credits retain every
earned mage-day, including energy beyond the base requirement. Resuming subtracts
these same credits from the goal. There is no separate progress ledger or second
shift. Reaching the base cost cannot roll early, create an item, or discard
remaining extra work. Abandonment still preserves daily rest and creates no item.

New Quick and Dirty settlement applies a single additional -1 if any
nonparticipant is within ten yards of the lead at resolution. Multiple people
still cause only one penalty. Current active square/hex placements and valid
Basic distance facts establish distance when available. For every remaining
same-location nonparticipant, including hidden actors, the trusted GM must supply
a current distance in the settlement command. Missing, duplicate, participant,
wrong-location, and map-contradicting observations are refused before the roll or
energy spend. Completed encounter maps and invalidated Basic facts cannot stand
in for current evidence. Revision, current GM seating, approved participants,
workspace and target custody are checked on the transaction's current state.

Creation validates the approved character catalog, concrete equipment specs,
target ownership and suitability, co-location, workspace, and the entire
material bill before consuming materials. Commands use the canonical revision,
receipt, event, and game-time ledgers. Restart or replay therefore cannot repeat
materials, project energy, time credit, charges, or the completion roll. The
private GM-authorized AdvanceEnchanting command advances only the named active
work's canonical clock, bounded by its deadline. Intermediate boundaries remain
subject to existing clock obligations. Grouped campaigns retain their separate
party timeline, and active combat must settle first. Seeded command reexecution covers creation, advancement,
settlement and a second project on both persistence stores.

Ordinary immediate actions and noncombat, nonceremonial spell channels may run
between scheduled Slow and Sure shifts. They use the recorded work calendar and
reject an activity that would continue into the next shift before committing
time, costs, rolls or a pending cast. A cast occupying the final second before a
shift can complete or cancel exactly at that boundary, using its persisted
identity and ready time. Both ordinary and project clocks preserve this pending
completion; neither can carry an unfinished cast into enchanting work. These
nighttime actions do not grant another daily shift or change project energy and
completion time. Both stores cover actual Staff construction, personal Light's
cost, target and illumination, retries, restart and seeded reexecution.

A successful completion attaches a provenance-bearing magic-item instance to
the target. It records effect identity, method, Power, mana-facing activation,
charges, maintenance, current owner, project, recipe, and runtime family. A
critical failure expends the target. Slow and Sure ordinary failure destroys an
unenhanced subject but preserves existing magic; Quick and Dirty failure retains
an explicit unresolved perversion for director adjudication. Transfers
carry the binding and update current ownership, while enchanted stacks cannot be
split.

New project records also checkpoint definite death or removal of any Slow and
Sure enchanter. Loss ends the project at the injury/removal checkpoint, preserves
all earlier injury and work records, and cannot be undone by later restoration
of the actor. The target is not destroyed merely because a mage was lost.
Temporary nighttime absence or unconsciousness is not treated as permanent loss.
New enrolled projects use full-state clock checkpoints even without a Cyclic
effect. A crossed heart-attack deadline ends work at that actual instant: death
at 120 during advancement to 115200 records loss/rest at 120 and the survivors'
next daily shift at 86400, without inventing extra days of work or rest.

Characters B235 applies to enchanting's special casting process: in very high
mana, even an ordinary failed roll produces the B481 critical-failure destruction.
The natural roll remains in the check trace. Quick and Dirty still spends every
promised contribution when rolling; each mage's FP returns at the next second
through the shared mana-refund ledger. Refunds add to the current pool within its
current limit and never restore HP. The extra setting-specific spectacular
disaster for a natural critical failure still needs an Enchant continuation; this
adapter does not invent another effect. Private command generations preserve old
recorded settlement behavior and exact retries without altering public schemas.
A separate energy/proximity generation preserves preexisting nearby behavior;
new default command fields are omitted from historical input and receipt bytes.
New enchanting refunds carry an internal clock generation so their deadline is
processed even after Quick and Dirty work has completed. Returning 9 FP at 3601
before a 2-FP Cyclic hit at 3610 leaves 8 FP and all 10 HP, instead of transiently
crossing zero FP and inflicting an erroneous HP loss. Unmarked historical refund
records retain their original timing and receipt identities.

Executable instances with the `spell` family enter the existing spell channel,
approved-build, mana, resistance, energy, effect, authority, perception, and
receipt path. Each new activation consumes at most one charge; replay consumes
none. Item Power receives the mana penalty once, uses listed casting time, and
does not acquire a personal caster's high-skill cost/time reductions. Missing
runtime families and depleted charges fail explicitly. Always-on items cannot be
driven through a cast channel. Physical breakage permanently records lost
enchantment identities, so ordinary repair cannot restore them; fresh enchantment
is independent of the old loss. See [item loss](gurps-magic-item-loss.md).

Staff construction records actual positive physical length and once-living
material. Personal Regular casts use an authenticated pointing intention and
separate current GM evidence for contact, then recompute source range/visibility
when the unresolved roll occurs. Real movement, custody changes and breakage
invalidate stale contact, including movement that returns to its starting hex.
Completed effects and retry receipts are not retroactively rescored.
For those personal casts, current Symptoms blindness and bilateral eye injury apply the
unseen penalty even for a remembered target; separately verified current touch
retains its source exception. Catalog-only Blindness purchases and magical sight
still lack executable sight consumers and remain unverified.
Regular/Resisted item casts now consume current physical sight in both execution
generations. Source-correct mapped Area targeting and Staff contact use the
fixed affected surface; see [Area item casting](gurps-area-item-casting.md).
Unimplemented magical sight remains open under #763/#785.
New spell command records carry a private targeting-generation marker. Commands
recorded before that marker reexecute their original path, and exact live retries
retain the original payload generation. Historical unresolved casts without a
targeting snapshot retain their recorded starting targeting. New casts capture
the source-correct roll-time targeting inputs without changing public commands.

The supported Enchant, Staff and Power baseline has construction and actual
project/item-use evidence; see [named-spell acceptance](gurps-enchantment-baseline.md).
A separate manufactured Staff/Deathtouch contact candidate is awaiting its own
full CI and merge verification; it is not credited here as merged evidence. Mapless proximity needs explicit trusted distances; no area is silently assumed empty.
Created Power above 100 still exceeds the completed-item schema's existing bound;
that is not a printed source cap, and such settlement remains unverified.
Grouped project-time progression and concurrent spellcasting during active
enchanting concentration still need their source consumers. Nighttime admission
for timed noncombat encounters, scene travel, physical procedures, combat and
ceremonial casting also remains under #785: those separate paths retain the
conservative project-busy refusal until their full intervals are joined.
Quick and Dirty
perversions require explicit director resolution. Power's outward zero-cast-cost
and missile/duplicate-Power compositions remain unverified; source-clear upkeep
and suitable wearer-effect behavior are separate. Bounded Haste now provides an
executable zero-casting-cost wearer effect; see [Haste and Power](gurps-haste-power-wearer.md). The section
ledger therefore remains partial, and the broader enchantment spell inventory is
not certified by these three named spells.

Buying and availability are campaign data (`MagicItemOffer`). The engine has no
global price-per-energy constant and does not promote the Basic Set's setting
examples into a universal market.

`tests/test_enchanting_energy_rules.py` verifies the extra-energy thresholds,
real mage-days, work continuing past full base progress, interruption/makeup,
current square/hex/Basic proximity, and actual Power, FP and HP consequences.
`tests/test_enchanting_energy_persistence.py` covers named Staff construction,
nearby settlement, current custody/approval/GM authority, exact concurrent retries,
restart, historical input bytes and full seeded reexecution on both stores.
`tests/test_enchanting_nighttime.py` verifies actual Staff work, nighttime Light,
the exact next-shift boundary, cancellation, both stores and seed reexecution.
`tests/test_enchanting_settlement.py` verifies named Staff loss at its actual
injury time, item destruction, current-pool refunds and historical generations.
`tests/test_enchanting_death_clock.py` covers crossed heart-attack death, both
stores, exact retries and full seeded reexecution.
`tests/test_enchanting_refund_clock.py` verifies refund-before-damage ordering,
completed-project clock activation and the explicit historical generation.

The selected sources are Campaigns, Fourth Edition, fourth printing, B480-482,
and Characters, Fourth Edition, third printing, B235 and B238.
Per the prerelease versioning policy, this change does not increment an engine
version.
