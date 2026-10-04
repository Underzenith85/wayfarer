# Critical enchanting Power privacy

B481 distinguishes the effective enchantment-skill rating of an item from the
named Power enchantment's energy discount. A critical enchanting success adds
2d to that rating; the enchanter knows the work went well, but the exact
improvement requires Analyze Magic.

The campaign and stream inventory projections now omit `power` only for the
binding whose project has an authoritative `enchantment-power:` critical-bonus
receipt. Other bindings on the same item remain visible, as do the known recipe,
activation mode and `power_reduction`. Existing persisted critical receipts are
covered without changing command inputs, canonical item ratings, events, public
schemas or replay generations. Scenario genesis cannot fabricate these private
execution receipts.

`tests/test_critical_item_power_privacy.py` exercises actual Haste manufacture and
added Power projects, the exact 17→22 regression, ordinary-rating controls,
critical Haste versus critical Power on the same item, actual item casting and
energy discount, player/spectator projections, drop/retrieval/transfer,
authority and stale refusal before dice, checkpoint rollback, restart, exact
retry, original-genesis seeded reexecution, and historical captured settlements.
The tests run on SQLite and PostgreSQL.

This is a correction to the two inventory projections, not Analyze Magic
execution. There is no supported discovery consumer yet, so ownership, recipe
knowledge, ordinary item use and an arbitrary world fact do not unlock the
inventory rating. Existing post-use item-casting `SpellResult` checks still
report numerical targets derived from canonical Power; that separate receipt
visibility policy remains unverified. Whole issue #785 remains open.
