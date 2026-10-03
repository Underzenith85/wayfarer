# Haste manufacture through canonical enchantment projects

This bounded B480–482 consumer manufactures ordinary, self-cast Haste armor, plain clothing and
jewelry items. It starts with an unenchanted individual item and runs the actual
Create/Begin/Advance/Settle project before the existing Haste host can spend FP
and change the wearer's Move and Dodge. It does not close the remaining #785
magic-enchantment criteria.

## Source and physical boundary

B482 specifies 250 enchantment energy per +1, armor/clothing or jewelry, and
self-only casting. B251 supplies the normal two-second cast, one-minute effect,
2 FP to cast and 1 FP to maintain per level, with levels 1–3. These items are not
inherently always on and do not inherently require Magery. Power is a separate
enchantment; this route neither adds a reduction nor substitutes for its project.

A seated, deployment-trusted GM may observe a specific existing recipe and
specific current inventory item through `ObserveHasteManufacture`. The initial
route requires a real armor profile and an individual intact wearable item,
with no weapon, shield, ammunition, container, or hand-slot contradiction.
Any existing Haste binding (including a historical non-executable binding) or
HasteItem record refuses the observed route before a new project or roll.
Re-enchantment/composition is unsupported; an observation cannot replace an
active carrier. Unselected historical projects retain their existing behavior. A
nonhand slot alone proves neither clothing nor jewelry. Plain clothing and jewelry require a separate trusted physical construction
record; their names and nonhand slots never supply classification.

The observation stores the exact canonical base recipe and a fingerprint of
its current inventory specification and pinned physical profile. The base recipe
uses `spell:haste`, its actual source effect, exactly 250 × levels energy, and
omitted runtime selectors. It must specify ordinary casting, no Magery
restriction, charges, Power reduction, or separately authored maintenance. The
public runtime spell enum and all public schemas remain unchanged.

## Captured project and settlement

Only a new Create command with a matching observation records the private
`haste_manufacture_generation` marker and creates an immutable project context.
Other new projects omit it. Recorded absence remains false on exact retries and
seeded reexecution, including when a valid observation was already present at
an old Create. An observation added later cannot retrofit a project.

Subsequent commands use the saved project context, never an ambient selector.
Productive Begin/Advance/Settle commands recheck the exact recipe, current item
identity, physical classification and condition. Interrupt/Abandon remain available after physical
conditions change, so cancellation can release the existing calendar work. Existing
project checks continue to require the approved Enchant/effect skill chain,
minimum 15 (20 in low mana), current lead ownership, enchanters, workspace,
materials, actual calendar work and current settlement authority.

On successful settlement the freshly produced project binding gains the private
Haste runtime family and an automatically generated HasteItem magnitude/form
record in the same command transaction. Failed settlement creates neither.
Rollback leaves project, item, metadata, receipts, stream and campaign unchanged.
Observation and project records occupy separate namespaces under the protected
`haste-manufacture:` genesis prefix. No manually installed artifact or genesis
HasteItem record supplies manufacturing acceptance.

For two ordinary enchanters, levels 1/2/3 require 125/250/375 workdays, ending
after the last eight-hour work interval. The resulting paid casts cost 2/4/6 FP,
last the normal minute, and add 1/2/3 to actual Move and Dodge. The existing
found-item and independently manufactured Power paths retain their behavior.

## Validation and limits

Dedicated tests exercise both SQLite and PostgreSQL actual projects, all three
paid magnitudes, normal failure, exact receipt retries, restart, full seeded
reexecution, captured absence, source-invalid recipes and physical forms,
authority, stale revision and rollback. Actual registered campaign and stream
projections hide private observations, project contexts and HasteItem metadata;
another player or spectator cannot see the owner's resulting binding. Existing
own-inventory binding visibility is retained; this is not a new claim about
critical unknown Power discovery.

Additional item forms, enchantment families, composition/re-enchantment and
broader discovery criteria remain open. No source catalog status
is promoted by this implementation.

## Plain clothing and jewelry producer

`DeclareHasteWearableConstruction` is a private, seated deployment-trusted GM
command. It binds an explicit B482 clothing/jewelry observation to one actual
individual item, its definition, approved wearable slot, and exact canonical
inventory specification and equipment profile. Classification is immutable.
A plain wearable has no armor, weapon mode, shield, ammunition or container
mechanics. Stacked, disabled, contained, grounded, hand-slot, already-Haste and
mismatched prototypes are rejected. The observation supplies physical evidence,
not a spell binding, skill bonus or permission to bypass project eligibility.

Only a new project with this exact observation captures generation 2. Generation
1 retains the original armor-only producer and unchanged serialized records;
an absent generation retains the historical non-executable result even when
an observation exists. Wrong generation/construction pairings refuse before
cost or dice. No public catalog, spell schema or movement package is extended.
The new `haste-wearable-construction:` prefix is private and protected against
injection into genesis. Seeded replay dispatches the authenticated private
construction command through the same transaction and authority gates.

`test_haste_wearable_manufacture.py` supplies actual plain body-slot clothing
and a distinct neck-slot pendant prototype, each at +1/+2/+3 on both stores.
The approved Enchant/Haste builds perform the full calendar project; the
resulting item drives actual paid FP/Move/Dodge and expiry. Further cases check
classification immutability, current custody/condition, failure, abandonment,
rollback, authority, stale requests, private campaign/stream projections,
restart/exact retries and full seeded replay, including recorded generation
absence. Neither item name nor a fabricated zero-DR armor profile is used.
