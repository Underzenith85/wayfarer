# Mundane trait and template inventory (#113)

The separate candidate package contains 65 selected mundane/background records
for the default finite campaign vocabulary. They include advantages,
disadvantages, perks, quirks, wealth, status, rank, additional spoken/written
languages, cultural familiarity, five relationship constructions, seven
appearance levels and two specific reputations. Each record identifies numeric
costs, exclusions, effect dependencies, source pages and manual obligations.
Changing identity vocabulary changes the package digest; an identity never
supplies a formula or arbitrary cost.

Seven original racial/occupational templates compose purchases through
`character.templates.TemplateCatalog` and the existing `CharacterCompiler`.
Envoy and Celebrated Envoy use bound traits and compile only when the campaign
pins their runtime hooks. Celebrated Envoy includes Envoy [20], Handsome [12]
and a +2 Bravery reputation [10], totaling 42 points. Selections, inclusion
cycles, duplicate purchases, unresolved references and taboo traits reject.
Templates and their compiler package pins have a digest; clients cannot override
totals or manufacture activation approvals.

## Executable effects and coverage matrix

Construction cost and executable effect stay separate. `mundane_traits.runtime`
binds effects to existing authoritative services. Fifteen records have selected
runtime bindings; this does not certify every rule or variant of those traits.

| Selected records | Bound effect | Reference | Remaining owner |
| --- | --- | --- | --- |
| Charisma | +1 reaction/influence per level, when perceived | B41 | #191 source reconciliation |
| Voice | +2 reaction when heard | B97 | #335 influence-skill bonus |
| Status, Low Status | Selected signed reaction/influence level | B28 | #334 relative standing/free Status |
| Bad Temper, Curious, Overconfidence | Approved self-control rating roll | B120-121, B124, B129, B148 | #333 consequences beyond the roll |
| Hideous, Ugly, Unattractive, Average, Attractive, Handsome | Appearance reactions, including Handsome's attraction split | B21 | #191; additional constructions #335 |
| Reputation (Bravery), Reputation (Cruelty) | +1/-1 reaction per purchased level, maximum four; everyone, always | B26-28 | #191; restricted/uncertain constructions #335 |

Appearance and reputation come from approved purchases and implemented pinned
hooks. The existing standing resolver supplies values; the existing transactional
social reducer owns rolls, receipts and visibility. Purchased appearance owns
the appearance source and purchased reputation owns the reputation source:
duplicate authored standing or raw modifiers reject before rolling, even under
another reputation ID. Actors without those purchases retain authored standing.
An unrelated authored source can coexist, including hidden reputation data.

Appearance requires sight and an observer whose race is affected; hearing a
character alone is insufficient. Charisma and Voice retain their own audiences.
Very Handsome is priced but unavailable because its resentment and nuisance
consequences are not implemented. Reputation records name concrete details;
these two selected constructions do not approximate class-scoped prices or
recognition-frequency discounts.

The other 50 records remain unavailable. Every record carries source owner #191;
concrete runtime follow-ups are exported directly to the source audit:

| Follow-up | Selected outstanding scope |
| --- | --- |
| #332 | Physical, combat, sensing, fatigue and healing effects/prerequisites |
| #333 | Mental and behavioral effects, manual obligations and relationships |
| #334 | Wealth, Status/Rank, language and culture interactions/constructions |
| #335 | Appearance and reputation variants; Voice influence-skill bonuses |

These follow-ups block remaining #113 gameplay coverage and #122 certification.
The inventory is a selected construction inventory, not an exhaustive Basic Set
index. Unselected entries remain #113/#191 reconciliation work. A manual ruling
or a priced construction never counts as complete runtime coverage.

## Pins, provenance and evidence

Candidate version 0.2.0 adds the standing constructions. No registry profile or
saved package pin is changed, and the package remains separate from frozen
profiles. Campaigns without its explicit runtime hooks cannot activate these
purchases. Public v1 contracts are unchanged.

Numeric references use Basic Set: Characters, Fourth Edition, third printing,
B21 and B26-28 for the new constructions. First-printing/2007-01-26 errata delta
verification remains #191; no profile is promoted to verified.

Independent tests cover construction costs, self-control multipliers, identities,
exclusions, template totals, unavailable effects, modifier values and audiences,
duplicate-source rejection, approved-build dispatch, hidden-source preservation
and receipt replay without re-resolving or consuming dice. Expected numeric
values are literals from the selected source pages, not generated from bindings.
