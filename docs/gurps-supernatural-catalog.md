# Basic Set supernatural catalog audit (#119)

The audit accounts for every spell in the Basic Set spell index, every exotic
or supernatural advantage/disadvantage row in the trait index, and the six
listed psi powers. **This completes inventory accounting, not supernatural
runtime coverage.** The selected-printing source review is reconciled. All 334
entries are verified against their completed implementation evidence after #688
closed the residual Fireball and Injury Tolerance mechanics. No campaign profile
or saved package is changed.

## Source boundary

`src/wayfarer/engine/rules/supernatural/inventory.json` records observed artifact hashes, edition,
printing, page references, classification and source evidence separately from
`gurps-4e-characters-3p-2008+campaigns-4p-2008`:

| Observed source | Index and rule references | Reconciliation |
| --- | --- | --- |
| Characters, Fourth Edition, third printing, February 2008 | B297–300 trait index; B304–334 spell index; B34–101 and B122–165 traits; B235–257 magic/psi | Selected baseline reconciled; no additional errata overlay. |
| Campaigns, Fourth Edition, fourth printing, 2008 | B479–482 enchantments and magic items, also indexed in Characters | Selected baseline reconciled; no additional errata overlay. |

The inventory contains identifiers and descriptive metadata, not rulebook prose
or an automatically executable transcription. Names, pages, difficulty, college
membership and psi Talent/modifier numbers were compared against the source
index and relevant sections. Complex index columns were visually inspected.
The four new ability-cost examples in `tests/test_supernatural_inventory.py`
were independently calculated from B46/B48/B61/B69/B101/B106/B111. These checks
supplement the existing execution tests; they do not certify every variant.

## Accounting boundary

| Inventory group | Rows | Coverage decision |
| --- | ---: | --- |
| Characters spells | 93 | Source, college and runtime implementations are verified, including Fireball's canonical missile/ranged path. |
| Campaigns enchantment spells | 7 | Enchant, Accuracy, Deflect, Fortify, Power, Puissance and Staff are verified required entries, not supplement exclusions. |
| Exotic/supernatural advantages | 146 | Includes all X and Sup index rows, even if an exotic trait could have a nonmagical origin. |
| Additional psi-member advantages | 4 | Animal Empathy, Danger Sense, Empathy and Resistant are mundane index entries explicitly listed in psi powers; included here for their psi use. Mundane construction is certified separately. |
| Exotic/supernatural disadvantages | 42 | Includes negative Destiny and Shadow Form as distinct definitions. Narrative or manual treatment never certifies a mechanical consequence. |
| Psi powers | 6 | Explicit member references, conditional membership, Talent cost and power modifier. No Antipsi Talent or power discount. |
| Magic protocols | 8 | Includes blocked class/area/ceremonial/item protocols and explicitly optional Clerical/Ritual Magic. |
| Transferred cinematic/supernatural skills | 28 | All explicit #112 exclusions retain named blockers #242/#243; not silently excluded from Basic. |
| **Total** | **334** | **334 verified.** |

A row covers its entire named definition, including levels, special modifiers
and conditional variants. Subtypes such as Morph, Alternate Form, Para-Radar
and Telesend are either their own indexed row or constrained members of a
parent definition. Index membership does not authorize an arbitrary combination.
Cross-college Breathe Water, Earth to Air and Hinder retain both colleges.
Mind-Reading the spell and Mind Reading the advantage have separate IDs.

Alternative magic systems are recorded as optional and rejected until a
reviewed exact profile selects them. Supplement spells, supplement power
systems and arbitrary user-created powers are outside this Basic Set inventory;
unknown IDs reject. The B257 permission to design other powers is not authority
for the LLM to invent their mechanics. General trait modifier construction stays
with #100; each family follow-up owns the concrete runtime consequences of its
special modifiers and combinations.

## Runtime and validator contract

The shared source-audit report directly consumes all 334 records,
including the 28 transferred skills and their optional-profile boundaries.
`inventory()` exposes typed immutable records. `lookup()` resolves exact audit
IDs, and `coverage_blockers()` exposes the item-level follow-up list. Source
references, unique IDs, psi members, evidence and blocker integrity are validated
when loading the package data, independently of the process working directory.

`require_entries(profile_id, ids)` rejects unknown profiles, out-of-scope Lite
requests, unknown IDs, optional systems and every unverified whole entry.
`require_verified()` in the shared conformance registry additionally invokes
this item gate for magic and supernatural families, so future row regressions
cannot be hidden by their verified family flags.

`definition()` supplies non-purchasable `audit:*` catalog records using existing
`RuleDefinition` and `ImplementationStatus.UNSUPPORTED`. Even with explicit
source permission, the existing `RulesCatalog.activate()` rejects them. No
second execution engine, frozen API change, profile publication or migration
is introduced. Existing representative spell/ability channels still use their
exact approved package validators; audit metadata does not enlarge their scope.

The supported-subset and evidence fields record narrower work honestly:
Light/Daze/Fireball/Create Fire execution version 2; purchased Foolishness,
Ignite Fire and Shape Fire prerequisites; profile v5 Magery learning; burning
Malediction, paid DR, rare Detect and shared-language Mind Reading; trusted
Mind Shield resistance context and every Injury Tolerance form. Fireball adds
held-missile Aim, exact hit-location targeting and completed ranged critical
consequences through shared services.

## Acceptance reconciliation

#117's representative lifecycle acceptance is satisfied by merged #171 / PR
#214, following PRs #172/#179/#183/#187. Source-compared final-turn casting,
critical failures, mana, HP costs, held missiles/Wait, fire crossings,
maintenance and persisted retries have executable evidence in
`test_spell_energy.py`, `test_spell_backfires.py`, `test_spell_execution.py`,
`test_spell_bindings.py` and `test_spell_service.py`.
#118's representative attack/defense/sensing/mental scope was already reconciled
in PR #187. Issue #688 completes the two residual inventory rows, so both family
capabilities are verified. Earlier conformance sections describe historical
implementation slices and do not override these acceptance decisions.

The completed implementation workstreams below own the row evidence. They no
longer appear as blockers merely to preserve history. Issue #688 reconciles the
former #107 Injury Tolerance and #173 Fireball residuals; unrelated #122 Basic
Set blockers remain visible in their owning inventories and ledgers.

| Owner | Exact family (entry lists are in each issue and inventory) |
| --- | --- |
| #221 | Seven enchantment spells |
| #222 | Knowledge, Light/Darkness and Meta spells |
| #223 | Movement and Protection/Warning spells |
| #224 | Healing spells |
| #225 | Necromantic and Gate spells |
| #226 | Air spells |
| #227 | Body Control spells |
| #228 | Fire spells, including remaining representative variants |
| #229 | Mind Control spells |
| #230 | Earth spells |
| #231 | Water spells |
| #232 | Communication/Empathy spells |
| #233 | Movement, body form and action-rate traits |
| #234 | Sensing, concealment and communication traits |
| #235 | Physiology, survival and recovery traits |
| #236 | Mental, spirit and fortune traits |
| #237 | Attack, defense and injury traits; residual Injury Tolerance completed by #688 |
| #238 | Jumper, Snatcher and Warp |
| #239 | Magery variants, mana and divine traits |
| #240 | Six psi powers, Talent, suppression and conditional members |
| #241 | Remaining magic class/ceremonial/area/item protocols and optional alternatives |
| #242 | Cinematic and enthrallment skills transferred from #112 |
| #243 | Alchemy, Herb Lore, Ritual Magic, Symbol Drawing and Thaumatology |

`tests/test_supernatural_inventory.py` independently fixes the complete spell
name set, supernatural advantage name set, source counts, cross-college
membership, psi exceptions and representative numeric expectations. It also
checks real evidence paths, malformed references, unsupported purchases and
family-gate bypass attempts. Existing execution suites continue to test concrete
runtime behavior; catalog accounting cannot replace them.
