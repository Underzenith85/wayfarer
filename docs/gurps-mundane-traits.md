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
binds effects to existing authoritative services. Twenty-seven records have selected
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

The other 38 records remain unavailable. Every record carries source owner #191;
concrete runtime follow-ups are exported directly to the source audit:

| Follow-up | Selected outstanding scope |
| --- | --- |
| #333 | Mental and behavioral effects, manual obligations and relationships |
| #334 | Wealth, Status/Rank, language and culture interactions/constructions |
| #335 | Appearance and reputation variants; Voice influence-skill bonuses |

These follow-ups block remaining #113 gameplay coverage and #122 certification.
The inventory is a selected construction inventory, not an exhaustive Basic Set
index. Unselected entries remain #113/#191 reconciliation work. A manual ruling
or a priced construction never counts as complete runtime coverage.

## Pins, provenance and evidence

Candidate version 0.3.0 adds physical bindings after the 0.2.0 standing constructions. No registry profile or
saved package pin is changed, and the package remains separate from frozen
profiles. Campaigns without its explicit runtime hooks cannot activate these
purchases. Public v1 contracts are unchanged.

Numeric references use the selected Basic Set: Characters, Fourth Edition,
third printing, B21 and B26-28. Item-level verification remains #191; no profile
is promoted to verified.

Independent tests cover construction costs, self-control multipliers, identities,
exclusions, template totals, unavailable effects, modifier values and audiences,
duplicate-source rejection, approved-build dispatch, hidden-source preservation
and receipt replay without re-resolving or consuming dice. Expected numeric
values are literals from the selected source pages, not generated from bindings.

## Physical trait execution (#332)

Candidate version **0.3.0** binds all 12 selected physical records, bringing the
selected inventory to **27 bound records and 38 unavailable records**. The earlier
0.2.0 standing scope above remains supported. Physical records retain #191 as a
source-certification blocker; #332 is no longer an unimplemented runtime owner.

| Records | Executable effects | Numeric reference |
| --- | --- | --- |
| Ambidexterity | Off-hand checks avoid the -4 penalty; existing two-weapon commitments remove only off-hand penalties and grant no extra attacks | B39 |
| Combat Reflexes | +1 armed/unarmed active defenses; +1 Fast-Draw; +2 Fright Checks; +6 IQ waking, surprise and mental-stun recovery; side initiative +1/+2, no total-surprise freeze | B43, B393 |
| Fit / Very Fit | +1/+2 HT rolls without changing HT or HT-based skills; five-minute ordinary FP recovery; Very Fit halves physical fatigue loss with durable fractional accounting | B55, B426-427 |
| High Pain Threshold | No injury shock; +3 knockdown/stunning resistance and physical-torture resistance, without reducing damage | B59, B420 |
| Night Vision | Cancels up to its level of partial-darkness attack/vision penalty, never the -10 for total darkness | B71 |
| Acute Hearing, Taste/Smell, Touch, Vision | +1 per level only to the selected Sense roll | B35 |
| Rapid / Very Rapid Healing | +5 natural-healing and crippling-duration HT checks; Very Rapid doubles natural HP healed; compiled HT 10/12 prerequisites | B79, B424 |

`PhysicalTraits` is a server-derived projection stored with HP. Initial resource
seeds cannot inject it; every loaded checkpoint compares it against the compiled
purchases and exact implemented hooks. Advancement updates it without healing
existing damage. Fitness changes require full FP and settled recovery so prior
fatigue cannot be reclassified. Unsupported profiles and unbound definitions
cannot supply this projection.

The existing injury, fatigue, medical, hazard, fright and combat reducers consume
it. Recovery tasks snapshot their rate and HT/healing bonuses, preserving earned
partial rest and replay across restarts. Power-spent FP is tracked separately:
neither its cost nor recovery is improved by fitness. Very-high-mana refunds
reduce that same debt. First Aid and Physician skills do not receive healing
trait bonuses; short-term stun/knockout recovery does not receive Rapid Healing.

`PhysicalCheckService` binds server-authored triggers for Sense, waking,
Fast-Draw, physical torture, off-hand and ordinary HT checks. Targets come from
approved attributes/skills and the projected trait. `SurpriseService` determines
side initiative and applies total/partial surprise before the first combat turn;
recovery runs at the start of existing injury turns. Both use the existing CAS
boundary, preserve receipts and keep detailed rolls in private resource events.

Battlefields may declare a bounded `darkness_penalty` (default zero), pinned into
the encounter. The authoring/scenario schemas explicitly include this additive
field and persisted physical state; generated schemas were regenerated and
reviewed. Frozen `/api/v1` play operations and fixtures are unchanged. The new
check/surprise adapters are internal APIs, not new player-supplied rule contexts.

Independent evidence is in `tests/test_physical_traits.py`, alongside existing
injury, recovery, melee, ranged, unarmed, fright and schema conformance suites.

## Mental, behavioral and relationship execution (#333)

Candidate version **0.4.0** binds Eidetic and Photographic Memory,
Single-Minded, Versatile, all three selected Shyness levels, Penetrating Voice,
Honesty and Truthfulness. The projection derives only from an approved build.
It exposes the exact contextual modifiers and automatic recall result; it never
chooses an action for the player. Failed Bad Temper, Curious, Overconfidence,
Honesty and Truthfulness checks now return a typed obligation naming the
required consequence beyond the existing self-control roll.

The relationship procedure records one 3d frequency roll (or no roll for a
constant relationship), validates stable person identities, rejects duplicate
constructions and requires a netted Ally/Dependent to share one frequency.
The five selected catalog constructions remain unavailable because their
manual relationship obligations and full construction-price variants are not
executable. Careful, Code of Honor (Soldier), and all selected Sense of Duty
scopes likewise remain explicitly manual and unavailable; they do not count as
verified coverage. Independent literal evidence is in
`tests/test_mental_traits.py` (Characters third printing B36, B51, B85, B96,
B101 and B124-159).
