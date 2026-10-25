# Repeated Power enchantment source review (#1012)

Direct review uses the uploaded Characters third printing (February 2008),
SHA-256 `872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`,
and Campaigns fourth printing (April 2008), SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
These match the selected compliance baseline. The PDFs and extracted text remain
outside the repository.

B480's Power spell reduces the casting and maintenance cost of spells on its
item by its purchased effect level. The level's enchanting energy is tabulated
separately from the enchantment's skill-based Power rating. B480 makes Enchant
permanent until destruction. B481 permits an item to carry any number of spells,
with a separate enchanting operation and separate skill-based Power for each,
and says an existing enchantment does not affect later enchanting work.

Characters B237's Limits on Effect ordinarily selects the strongest result for
repeated variable spells on a subject. The same paragraph expressly excepts
spells that permanently affect the subject, allowing their full effect on each
application. Applying the ordinary strongest-only rule to permanent enchantment
without accounting for this exception would omit relevant source text.

These passages do not explicitly define the repeated-Power operation: independent
additive effect levels, replacement, upgrading an existing level, a full or
incremental energy bill, and the identity/rating of the surviving bindings are
not specified together. The generic permanent-effect exception alone is
insufficient to choose an enchanting upgrade and binding contract. This review
therefore does not introduce a numerical stacking expectation or treat the
ordinary strongest-only rule as settled enchantment behavior.

The existing bounded implementation continues to refuse an additional Power
project when the target retains a Power enchantment or a legacy inline energy
discount. Item consumers refuse multiple passive Power sources or mixed passive
and inline discounts. Lost enchantments are excluded; independently scoped
legacy inline discounts retain their existing scope. These are explicit
unsupported-operation guards, not a claim that the source prohibits repeated
Power or that #1012 is complete. `tests/test_magic_item_lifecycle.py` checks those
guards, item-wide ordinary Power, mana eligibility and irreversible magic loss.
No source-ledger or certification status changes follow from this review.
