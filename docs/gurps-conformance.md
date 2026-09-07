# GURPS Fourth Edition conformance baseline

Issue #95 freezes the source boundary and evidence contract for the GURPS mechanics track. It does **not** certify the existing Wayfarer prototype as GURPS-conformant and it does not copy rulebook prose.

## Frozen source artifacts

The baseline selects the 2004 Fourth Edition line. Revised editions, later printings, supplements and FAQ interpretations are excluded unless an explicit baseline revision adds them.

| Source ID | Frozen artifact | Errata revision |
| --- | --- | --- |
| `sjg:gurps-lite-4e-2004` | August 2004 electronic edition, Rev. 07/12/04 | No separate errata overlay selected |
| `sjg:basic-set-characters-4e-2004` | 2004 Fourth Edition, first printing | [First-printing errata](https://www.sjgames.com/errata/gurps/4e/basic-set-characters.html), January 26, 2007 |
| `sjg:basic-set-campaigns-4e-2004` | 2004 Fourth Edition, first printing | [First-printing errata](https://www.sjgames.com/errata/gurps/4e/basic-set-campaigns.html), January 26, 2007 |

These are deliberate historical targets, not claims about the latest PDFs. Official source entry points: [Lite](https://www.sjgames.com/gurps/lite/) and [Basic Set](https://www.sjgames.com/gurps/books/basic/). Source metadata is recorded in `tests/fixtures/gurps/conformance.json`; no rulebook text is bundled. The full source artifacts and errata contents have not been audited in this change. Exact targets are specified, but source review remains a merge blocker (see below).

## Profiles

`gurps-lite-4e-2004` is the first certification target. Every capability marked `lite_required` must eventually become `verified`; `partial`, `manual`, or `absent` blocks Lite certification.

`gurps-basic-set-4e-2004` is the broader Basic Set target. Every capability marked `basic_required` must eventually become `verified`. Catalog audits may add capabilities; additions are blockers until implemented or explicitly removed from scope in a reviewed profile revision.

The original `package:wayfarer-lite` remains a separate prototype rules package. It is not renamed or treated as either GURPS profile.

## Coverage states

The machine-readable inventory uses exactly four states:

- `absent`: no authoritative implementation exists.
- `partial`: Wayfarer has related behavior, but it is not complete evidence for the selected GURPS mechanic.
- `manual`: the engine can expose or record the concept but does not execute the mechanic authoritatively.
- `verified`: independent source-referenced fixtures and implementation tests agree for the selected profile.

A generic hook, similarly named prototype mechanic, or LLM ruling cannot promote an entry to `verified`.

## Capability IDs and fail-closed behavior

Capabilities use stable dotted IDs under the `gurps.` namespace. `wayfarer.rules.conformance.capability()` rejects unknown identifiers and `require_verified()` rejects every status except `verified`. `require_capabilities(profile_id, capability_ids)` additionally rejects unknown profiles and capabilities outside the selected profile, including when the requirement list is empty. Scenario validators, character validators, action proposal validation, and future profile APIs must resolve mechanics through this registry (or a generated equivalent) before allowing the LLM to propose them.

This means unsupported mechanics cannot be invented simply because a prompt names them. New mechanics first require a reviewed capability entry, source mapping, implementation owner, and independent expected-result fixtures.

## Fixture contract

`tests/fixtures/gurps/conformance.json` is an independent expectation ledger, not generated from implementation output. Each case contains:

- a stable fixture ID and capability ID;
- a profile and source reference;
- page/section metadata without copied prose;
- explicit numeric inputs and expected outputs;
- units where relevant;
- a rounding rule where relevant;
- a note when current Wayfarer prototype behavior intentionally diverges.

Expected values must be entered from the frozen source by the PR author/reviewer. Tests may consume those values but may never overwrite or derive them from the implementation under test.

## Numeric conventions

Unless a capability-specific fixture says otherwise:

- distance is stored in yards;
- mass is stored in pounds;
- time is stored in seconds;
- monetary values use the rules profile's abstract `$` value as integer units;
- dice expressions are structured as count, sides, and integer add rather than pre-rolled totals;
- fractions remain exact until the source-defined rounding point;
- source-defined rounding is represented explicitly in the fixture as `floor`, `ceil`, `nearest`, `truncate`, or `none`.

Implementations must not introduce implicit banker’s rounding or binary floating-point rounding where the selected rule specifies a different result.

## Updating coverage

Every mechanics PR in #94 must update the inventory and add independent cases for each capability it moves toward `verified`. A capability can be marked `verified` only in the same PR that supplies executable evidence. Catalog audits (#112, #113, #114, #119) may expand the inventory and should open bounded follow-up issues when they discover runtime behavior that does not fit their PR.


## Optional rules and integration boundary

No optional rule is enabled by default, and arbitrary optional-rule names are not accepted. Cinematic rules, influencing success rolls, bleeding, accumulated wounds, extra effort in combat and optional magic/psi systems need individually identified capability entries and a reviewed profile revision before activation. Broad family entries below describe future implementation targets, not permission to enable every variant. Tactical hex rules are a Basic target and are outside Lite. A future profile integration (#96) must preserve this distinction and record all enabled options explicitly.

These helpers expose a fail-closed contract for future scenario/character validators. Existing validators and campaign persistence still use the prototype package; this PR does not claim that they are already wired to a new GURPS runtime. #96 owns selection and migration, and mechanics implementation belongs to its existing owners.

## Independent evidence

The test suite executes seven fixed success/critical examples through the existing authoritative check service. This demonstrates agreement only for those examples; coverage remains partial. A separate both-fail Quick Contest case records the published winner and the prototype's different result. It deliberately passes only while that documented divergence remains, so #99 must update both evidence and coverage when implementing the published behavior. The unknown-capability test is an application contract test, not a rulebook-derived mechanic.

## Outstanding acceptance blockers

The following matrix is a **mechanics-family inventory**, not an exhaustive catalog audit. Issue #95 must remain open until a reviewer with the selected source artifacts accounts for every rule and catalog entry, confirms page references and errata effects, and records exclusions individually. Do not infer completeness from the row count or from green tests.

The existing catalog work in #112 (traits), #113 (skills), #114 (equipment), and #119 (creatures/templates) must supply item-level inventories; those cannot be replaced by a generic family row. Missing item identifiers fail closed today. Source-artifact review and the exhaustive inventory are outstanding prerequisites for marking #95 complete, not deferred certification work.

## Mechanics-family coverage matrix

Status and implementation ownership mirror `CAPABILITIES`. None is certified. References name source sections without reproducing prose; precise item/page verification remains part of the source audit above.

| Capability | Lite required | Basic required | State | Owner |
| --- | --- | --- | --- | --- |
| `gurps.character.primary_attributes` | yes | yes | partial | #97 |
| `gurps.character.secondary_characteristics` | yes | yes | absent | #97 |
| `gurps.character.skill_difficulty` | yes | yes | partial | #98 |
| `gurps.character.skill_defaults` | yes | yes | absent | #98 |
| `gurps.character.specialties` | no | yes | absent | #98 |
| `gurps.character.techniques` | no | yes | absent | #98 |
| `gurps.character.traits` | yes | yes | manual | #100 |
| `gurps.character.self_control` | yes | yes | absent | #100 |
| `gurps.character.ability_modifiers` | no | yes | absent | #100 |
| `gurps.check.success` | yes | yes | partial | #99 |
| `gurps.check.margin` | yes | yes | partial | #99 |
| `gurps.check.critical` | yes | yes | partial | #99 |
| `gurps.check.quick_contest` | yes | yes | absent | #99 |
| `gurps.check.regular_contest` | no | yes | absent | #99 |
| `gurps.check.resistance` | yes | yes | absent | #99 |
| `gurps.social.reaction` | yes | yes | absent | #111 |
| `gurps.social.influence` | yes | yes | absent | #111 |
| `gurps.social.fright` | no | yes | absent | #111 |
| `gurps.equipment.weapon_profiles` | yes | yes | partial | #101 |
| `gurps.equipment.armor_profiles` | yes | yes | partial | #101 |
| `gurps.equipment.catalog` | yes | yes | partial | #114 |
| `gurps.equipment.object_durability` | no | yes | absent | #114 |
| `gurps.injury.damage_types` | yes | yes | partial | #102 |
| `gurps.injury.damage_resistance` | yes | yes | partial | #102 |
| `gurps.injury.hp_thresholds` | yes | yes | partial | #102 |
| `gurps.injury.hit_locations` | no | yes | absent | #107 |
| `gurps.injury.armor_divisors` | no | yes | absent | #107 |
| `gurps.injury.lasting_wounds` | no | yes | absent | #107 |
| `gurps.combat.melee_attack` | yes | yes | partial | #103 |
| `gurps.combat.active_defense` | yes | yes | partial | #103 |
| `gurps.combat.maneuvers` | yes | yes | partial | #104 |
| `gurps.combat.turn_timing` | yes | yes | partial | #104 |
| `gurps.combat.ranged_attack` | yes | yes | partial | #106 |
| `gurps.combat.aim` | yes | yes | absent | #106 |
| `gurps.combat.ammunition` | yes | yes | absent | #106 |
| `gurps.combat.rapid_fire` | no | yes | absent | #106 |
| `gurps.combat.unarmed` | yes | yes | partial | #108 |
| `gurps.combat.grappling` | yes | yes | absent | #108 |
| `gurps.tactical.hex_movement` | no | yes | absent | #105 |
| `gurps.tactical.facing` | no | yes | absent | #105 |
| `gurps.tactical.visibility` | no | yes | absent | #105 |
| `gurps.recovery.fatigue` | yes | yes | partial | #109 |
| `gurps.recovery.healing` | yes | yes | partial | #109 |
| `gurps.recovery.medical_treatment` | no | yes | absent | #109 |
| `gurps.world.physical_feats` | yes | yes | partial | #110 |
| `gurps.world.environmental_hazards` | yes | yes | absent | #110 |
| `gurps.magic.spellcasting` | no | yes | absent | #117 |
| `gurps.supernatural.abilities` | no | yes | absent | #118 |
| `gurps.vehicles.movement` | no | yes | absent | #120 |
| `gurps.vehicles.combat` | no | yes | absent | #120 |
