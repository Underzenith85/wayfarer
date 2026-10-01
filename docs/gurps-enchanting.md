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
Quick and Dirty assistant penalties and extra ceremonial energy affect the
result, while low mana is temporary. Natural 16 fails; 17-18 critically fail.

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

A successful completion attaches a provenance-bearing magic-item instance to
the target. It records effect identity, method, Power, mana-facing activation,
charges, maintenance, current owner, project, recipe, and runtime family. A
critical failure expends the target. Slow and Sure ordinary failure destroys an
unenhanced subject but preserves existing magic; Quick and Dirty failure retains
an explicit unresolved perversion for director adjudication. Transfers
carry the binding and update current ownership, while enchanted stacks cannot be
split.

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
Item-cast current-sight consumers remain a separate open part of #763/#785.
New spell command records carry a private targeting-generation marker. Commands
recorded before that marker reexecute their original path, and exact live retries
retain the original payload generation. Historical unresolved casts without a
targeting snapshot retain their recorded starting targeting. New casts capture
the source-correct roll-time targeting inputs without changing public commands.

Issue #785 remains the live owner of incomplete named-spell/item acceptance.
Staff's held Melee-spell carrier requires the missing #747 runtime family.
Grouped project-time progression, concurrent spellcasting during active
enchanting concentration, and normal nighttime activity while a project stays
scheduled still need their source consumers; the current general busy-actor
guard is conservative. Quick and Dirty
perversions require explicit director resolution. Power's outward zero-cast-cost
and missile/duplicate-Power compositions remain unverified; source-clear upkeep
and suitable wearer-effect behavior must be assessed separately. The section
ledger therefore remains partial, and the broader enchantment spell inventory is
not certified by these three named spells.

Buying and availability are campaign data (`MagicItemOffer`). The engine has no
global price-per-energy constant and does not promote the Basic Set's setting
examples into a universal market.

The selected source is Campaigns, Fourth Edition, fourth printing, B480-482.
Per the prerelease versioning policy, this change does not increment an engine
version.
