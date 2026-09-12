# GURPS Basic Set enchanting projects

Issue #526 implements Campaigns B480-482 as authored, persisted projects. It
does not add spell formulas to the college catalog owned by #221. A recipe names
that cataloged spell, its target equipment definitions, explicit enchanting
method, energy, materials, workspace, activation, Power adjustments, charge or
maintenance shape, and an optional registered runtime family.

Quick and Dirty work takes one hour per 100 energy (rounded up), requires an
explicit contribution from every enchanter, applies the assistant penalty, and
spends the promised FP/HP only when the final roll is made. Slow and Sure work
contributes one energy per full eight-hour mage-day; interruption credits only
completed mage-days and persists the two-day make-up delay. Both methods use the
lower approved level of Enchant and the effect spell. Assistants require both at
15+, or 20+ in low mana, and item Power below 15 is rejected before work begins.

Creation validates the approved character catalog, concrete equipment specs,
target ownership and suitability, co-location, workspace, and the entire
material bill before consuming materials. Commands use the canonical revision,
receipt, event, and game-time ledgers. Restart or replay therefore cannot repeat
materials, project energy, time credit, charges, or the completion roll.

A successful completion attaches a provenance-bearing magic-item instance to
the target. It records effect identity, method, Power, mana-facing activation,
charges, maintenance, current owner, project, recipe, and runtime family. A
critical failure expends the target; an ordinary failure preserves it. Transfers
carry the binding and update current ownership, while enchanted stacks cannot be
split.

Executable instances with the `spell` family enter the existing spell channel,
approved-build, mana, resistance, energy, effect, authority, perception, and
receipt path. Each new activation consumes at most one charge; replay consumes
none. Missing runtime families and depleted charges fail explicitly. Always-on
items cannot be driven through a cast channel.

Buying and availability are campaign data (`MagicItemOffer`). The engine has no
global price-per-energy constant and does not promote the Basic Set's setting
examples into a universal market.

The selected source is Campaigns, Fourth Edition, fourth printing, B480-482.
Per the prerelease versioning policy, this change does not increment an engine
version.
