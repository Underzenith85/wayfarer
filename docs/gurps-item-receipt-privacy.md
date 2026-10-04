# Critical item casting receipt privacy

B481 keeps a critical enchantment's exact improvement private. Its permanent
item Power continues to drive canonical casting checks, mana eligibility and
consequences; the separate Power enchantment still determines energy discounts.

Authenticated player Haste and general spell receipt presentation omits the
entire `checks` tuple when the accepted source binding has a canonical critical
`enchantment-power:` receipt. Targets, margins and threshold classifications
cannot reconstruct the rating from the remaining diagnostic trace. Actual
outcome and energy remain observable. Trusted GM diagnostics and noncritical
item or learned-spell checks retain their existing values.

Selection uses the command's original revision, so a later transfer, item
replacement or charge change cannot reclassify an old exact retry. Haste uses
its explicit item binding ID; the general spell path follows the first surviving
matching canonical item binding. Stored inputs, spell events, checks, item
ratings, randomness and replay are unchanged. No new public field or discovery
claim is introduced.

Critical diagnostics remain private even after an Analyze Magic report; making
them visible only after a truthful report would expose whether an apparent
report was false. This policy creates no Analyze Magic consumer or automatic
ownership/use-based discovery.

`test_item_receipt_privacy.py` uses actual B482 Haste manufacture, paid activation,
critical success/failure, ordinary controls, player/GM authority, stale refusal,
forced checkpoint rollback, transfer, restart, exact retries and original-genesis
seeded reexecution on SQLite and PostgreSQL. Whole #785 remains open.

`test_critical_fireball_item_receipts.py` separately proves the general service
with an actual B482 800-energy Fireball staff project, its consumed material
budget, mage-only binding and paid activation. Success and failure preserve
canonical checks and FP while returning private player diagnostics and complete
GM diagnostics. The oracle includes exact retry, restart and full seeded replay
on both stores; an unchanged older service disclosed the actual rating. Haste
is not added to the public general-spell vocabulary to obtain this proof.
