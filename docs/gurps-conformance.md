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

These helpers expose a fail-closed contract for scenario/character validators. #96 wires selection through them: `wayfarer.rules.profiles` registers `profile:gurps-lite-4e-2004@2` and `profile:gurps-basic-set-4e-2004@2` with exactly the required capability sets above, and a profile is selectable only when every required capability is `verified`. Today neither GURPS profile is selectable; new campaigns that name one are rejected with the unverified capability list, existing campaigns keep the prototype pins, and switching a paused campaign requires the explicit migration described in [rules profiles](rules-profiles.md). Mechanics implementation still belongs to the owners in the matrix, and each mechanics PR must move its capabilities to `verified` before its profile can activate.

## Attributes and secondary characteristics (#97)

`wayfarer.character.statistics` owns primary attributes and secondary characteristics for the two profiles. It is selected only by an exact profile ID (`gurps-lite-4e-2004` or `gurps-basic-set-4e-2004`) passed to `CharacterCompiler(statistics_profile=...)`; the prototype `package:wayfarer-lite` is not a profile, compiles exactly as before, and keeps its recorded build revisions (a regression test pins two of them). A compiler without a profile refuses any package that carries `secondary:*` definitions, and a compiler with a profile refuses packages whose attribute and secondary definitions do not match the profile's costs. The compiler also calls `require_capabilities` for both #97 capabilities, so the registry status gates activation. The catalog side (identifiers, per-level costs, table bounds) lives in `wayfarer.rules.gurps_characters`; version 0.2.0 of the registered GURPS Lite and Characters packages carries exactly those definitions, and the runtime engine factory passes each registered profile's conformance target to the compiler. Both GURPS profiles remain unsupported until their other required capabilities are verified, so no campaign can select them yet.

Implemented under both profiles, as catalog purchases whose `amount` is the purchased absolute level:

- `attribute:st|dx|iq|ht`: per-level costs from 10; level 1 is the floor, the campaign policy ceiling still applies.
- `secondary:hp`, `secondary:will`, `secondary:per`, `secondary:fp`: independently purchased; unpurchased values default to ST, IQ, IQ and HT at no cost.
- `secondary:basic-speed`: purchased in quarter units (an `amount` of 23 is 5.75) because the source sells it in 0.25 steps; the default equals DX + HT quarters. Fractions are kept exactly.
- `secondary:basic-move`: default is Basic Speed with the fraction dropped (`floor`), then purchased per yard/second.
- Derived, not purchasable: Dodge (Basic Speed + 3, fraction dropped), Basic Lift (ST squared over five, in pounds; `nearest` from 10 up, exact fraction below, no tie is possible), encumbrance thresholds at 1, 2, 3, 6 and 10 times Basic Lift, encumbered Move (`floor`, minimum 1 when Basic Move is at least 1), encumbered Dodge, and thrust/swing damage from the ST table.

The typed projection (`CharacterStatistics`) carries build values and point costs only. Runtime pools stay in `ResourceState`; the only bridge is `carry_over`, which preserves the existing deficit when a ceiling moves. `pool_limits(build)` is the single place that decides which sheet targets initialize HP and FP (`secondary:hp`/`secondary:fp` for profile builds, `attribute:st`/`attribute:ht` for the prototype), and advancement uses it so recompilation and purchases never heal an injured character (a replacement character still starts full). Effects may target `secondary:*` values through the existing evaluator.

Fail-closed and advisory boundaries:

- Damage lookup accepts only listed ST rows: 1 to 20 under the Lite profile, 1 to 40 and the listed five-point steps to 100 under Basic. Anything else is a `damage.unsupported_st` diagnostic rather than an interpolated guess.
- The source's GM-permission guidelines (HP or FP more than 30% away from ST or HT, Will or Per above 20) are reported as `advisories` on the projection. They are not hard failures and not silently ignored; the power reviewer can turn them into review findings.
- Engine invariants that are not rules claims: HP, FP, Will and Per compile to at least 1, Basic Move to at least 0.
- Not implemented: the Basic Set Size Modifier discount on ST and HP costs. It is recorded as the Basic-only capability `gurps.character.size_modifier_costs` (`absent`) so it stays a visible certification blocker until a named follow-up implements it; Lifting ST, Striking ST and similar traits belong to #100.

Fixture cases for both capabilities live in `tests/fixtures/gurps/conformance.json` with an `operation` field naming the executable check; `tests/test_statistics.py` runs every one of them plus property tests for fraction handling, rounding, load bands and pool carry-over.

## Independent evidence

`tests/test_gurps_conformance.py` drives every check, contest and resistance case in the fixture ledger through the profile-selected services in `wayfarer.rules.gurps_checks` with recorded dice, and asserts that each service consumed exactly the recorded dice. The both-fail Quick Contest case keeps its `prototype_expected` record: the prototype `package:wayfarer-lite` contest still discards failed rolls, the GURPS profile compares margins of failure, and the test fails if either behaviour drifts. `tests/test_gurps_checks.py` adds Hypothesis invariants (outcome boundaries, contest antisymmetry, levelling bounds, Rule of 16 cap) plus replay and fail-closed negative paths. The unknown-capability and unknown-profile tests are application contract tests, not rulebook-derived mechanics.

Expected values in the ledger were entered by hand from the frozen sources and then checked against the implementation, never the reverse. Page references name sections without reproducing prose; confirming them against the physical artifacts remains part of the source audit below.

## Success, contest and resistance services (#99)

`wayfarer.rules.gurps_checks` implements the `gurps.check.*` capabilities on top of the existing authoritative scorer `wayfarer.rules.checks.evaluate_success`; there is no second dice engine. Every entry point takes an exact profile ID and calls `require_capabilities`, so an unknown profile, a capability outside the profile (Regular Contests on the Lite profile) or an unverified capability is rejected before any die is drawn.

| Service | Behaviour | Receipt |
| --- | --- | --- |
| `success_roll` | 3d6 against base target plus typed modifiers; margin is target minus total; critical success on 3-4, on 5 at effective 15+, on 6 at effective 16+; critical failure on 18, on 17 at effective 15 or less, or on failure by 10 or more; 17 always fails | `CheckTrace` with `rules_package` = profile, `rules_version` = baseline, `rule_id` = capability |
| `repeated_attempt` | Declared policy: single chance, retry until success, unknown until later, hazardous failure. Later attempts are rejected under single chance, unknown until later, or after a success; hazardous failures are flagged; unknown outcomes are marked unrevealed | `AttemptTrace` with attempt number, policy, reveal and hazard flags |
| `quick_contest` | One roll each. Success beats failure; otherwise the larger margin of success or the smaller margin of failure wins; equal margins tie. Margin of victory is the difference between the two margins | `QuickContestTrace` with both traces, winner, decision reason and margin of victory |
| `regular_contest` | Rounds continue until exactly one contestant succeeds. When both effective skills exceed 14 both are lowered so the higher becomes 14; when both are below 6 both are raised so the lower becomes 6, recorded as a `contest-adjustment` modifier. An undecided contest beyond the round limit raises instead of guessing | `RegularContestTrace` with every round and the levelling adjustment |
| `resistance_roll` | Quick Contest of attacker against resister; the resister wins ties. With `rule_of_16` declared, the attacker's effective skill is capped at the higher of 16 and the resister's effective resistance, recorded as a `rule-of-16` modifier. The flag is rejected on the Lite profile because the frozen Lite artifact has no resisted supernatural attacks | `ResistanceTrace` with the contest and `affected` |

Typed modifiers carry a `ModifierKind` (`situational`, `equipment`, `trait`, `time`, `repeated-attempt`, `contest-adjustment`, `rule-of-16`). Existing prototype call sites default to `situational`; persisted receipts gain the field with that default and are not migrated. Cumulative repeated-attempt penalties are GM rulings and are supplied as `repeated-attempt` modifiers rather than invented by the engine.

Replay: `replay_success`, `replay_quick_contest`, `replay_regular_contest` and `replay_resistance` re-score the dice recorded in a receipt through `RecordedDice`, which refuses to invent a die and reports any unused die. Tests prove the replayed trace equals the original and that a live random source is never consulted.

Explicit engine interpretations, recorded here because the frozen sources do not decide them:

- A 17 or 18 at effective skill 17 or more is a failure whose numeric margin is simply target minus total; the sources define no separate margin of failure for automatic failures, so no fixture pins one.
- Critical results have no separate clause in the published Quick Contest text, so contests decide by margin only; a critical success can lose to a larger ordinary margin.
- Margin of victory when both contestants fail is the difference of their margins of failure, extending the published mixed and both-succeed definitions.

Integration boundary: campaign play still resolves through the prototype package. Selecting a GURPS profile for a saved campaign is the explicit migration in [rules profiles](rules-profiles.md), which stays rejected until every required capability of that profile is verified; these services are ready for it and for the scenario/character validators, but nothing in this change alters existing campaign behaviour.

## Outstanding acceptance blockers

The following matrix is a **mechanics-family inventory**, not an exhaustive catalog audit. Issue #95 must remain open until a reviewer with the selected source artifacts accounts for every rule and catalog entry, confirms page references and errata effects, and records exclusions individually. Do not infer completeness from the row count or from green tests.

The existing catalog work in #112 (traits), #113 (skills), #114 (equipment), and #119 (creatures/templates) must supply item-level inventories; those cannot be replaced by a generic family row. Missing item identifiers fail closed today. Source-artifact review and the exhaustive inventory are outstanding prerequisites for marking #95 complete, not deferred certification work.

## Mechanics-family coverage matrix

Status and implementation ownership mirror `CAPABILITIES`. None is certified. References name source sections without reproducing prose; precise item/page verification remains part of the source audit above.

| Capability | Lite required | Basic required | State | Owner |
| --- | --- | --- | --- | --- |
| `gurps.character.primary_attributes` | yes | yes | verified | #97 |
| `gurps.character.secondary_characteristics` | yes | yes | verified | #97 |
| `gurps.character.size_modifier_costs` | no | yes | absent | #97 (follow-up) |
| `gurps.character.skill_difficulty` | yes | yes | verified | #98 |
| `gurps.character.skill_defaults` | yes | yes | verified | #98 |
| `gurps.character.specialties` | no | yes | verified | #98 |
| `gurps.character.techniques` | no | yes | verified | #98 |
| `gurps.character.traits` | yes | yes | partial | #100, #113 ([selected construction inventory](gurps-mundane-traits.md)) |
| `gurps.character.self_control` | yes | yes | partial | #100 |
| `gurps.character.ability_modifiers` | no | yes | partial | #100 |
| `gurps.check.success` | yes | yes | verified | #99 |
| `gurps.check.margin` | yes | yes | verified | #99 |
| `gurps.check.critical` | yes | yes | verified | #99 |
| `gurps.check.quick_contest` | yes | yes | verified | #99 |
| `gurps.check.regular_contest` | no | yes | verified | #99 |
| `gurps.check.resistance` | yes | yes | verified | #99 |
| `gurps.social.reaction` | yes | yes | partial | #111 |
| `gurps.social.influence` | yes | yes | partial | #111 |
| `gurps.social.fright` | no | yes | partial | #111 |
| `gurps.equipment.weapon_profiles` | yes | yes | partial | #101 (typed schema and inventory adapter; source audit pending) |
| `gurps.equipment.armor_profiles` | yes | yes | partial | #101 (typed schema and inventory adapter; source audit pending) |
| `gurps.equipment.catalog` | yes | yes | partial | #114 |
| `gurps.equipment.object_durability` | no | yes | absent | #114 |
| `gurps.injury.damage_types` | yes | yes | partial | #102 |
| `gurps.injury.damage_resistance` | yes | yes | partial | #102 |
| `gurps.injury.hp_thresholds` | yes | yes | partial | #102 |
| `gurps.injury.hit_locations` | no | yes | partial | #107; [living-human dispatch and blockers](gurps-hit-locations.md) |
| `gurps.injury.armor_divisors` | no | yes | partial | #107; [numeric armor integration](gurps-hit-locations.md) |
| `gurps.injury.lasting_wounds` | no | yes | partial | #107; [durable impairments and remaining effects](gurps-hit-locations.md) |
| `gurps.combat.melee_attack` | yes | yes | partial | #103 |
| `gurps.combat.active_defense` | yes | yes | partial | #103 |
| `gurps.combat.maneuvers` | yes | yes | partial | #104; [executable transitions and remaining scope](gurps-maneuvers.md) |
| `gurps.combat.turn_timing` | yes | yes | partial | #104 |
| `gurps.combat.ranged_attack` | yes | yes | partial | #106; [ranged dispatch and evidence](gurps-ranged.md); remaining #173 |
| `gurps.combat.aim` | yes | yes | partial | #104; target-bound accumulation and disruption; ranged resolution #106 |
| `gurps.combat.ammunition` | yes | yes | partial | #106; [reservations and reload timing](gurps-ranged.md); remaining #173 |
| `gurps.combat.rapid_fire` | no | yes | partial | #106; [burst and Dodge resolution](gurps-ranged.md); remaining #173 |
| `gurps.combat.unarmed` | yes | yes | partial | #108 |
| `gurps.combat.grappling` | yes | yes | absent | #108 |
| `gurps.tactical.hex_movement` | no | yes | partial | #105 |
| `gurps.tactical.facing` | no | yes | partial | #105 |
| `gurps.tactical.visibility` | no | yes | partial | #105 |
| `gurps.recovery.fatigue` | yes | yes | partial | [#109 details](gurps-recovery.md) |
| `gurps.recovery.healing` | yes | yes | partial | [#109 details](gurps-recovery.md) |
| `gurps.recovery.medical_treatment` | no | yes | partial | [#109 details](gurps-recovery.md) |
| `gurps.world.physical_feats` | yes | yes | partial | #110; [bounded authoritative procedures](gurps-hazards.md) |
| `gurps.world.environmental_hazards` | yes | yes | partial | #110; [persistent exposure schedules](gurps-hazards.md) |
| `gurps.magic.spellcasting` | no | yes | absent | #117 |
| `gurps.supernatural.abilities` | no | yes | absent | #118 |
| `gurps.vehicles.movement` | no | yes | absent | #120 |
| `gurps.vehicles.combat` | no | yes | absent | #120 |

Profile registration and explicit migration (#96) are infrastructure, not
mechanics: they add no row and change no state above. Both GURPS profiles remain
unsupported while any `lite_required` or `basic_required` entry is still
`absent`, `partial` or `manual`; the verified `gurps.check.*` rows alone do not
make either profile selectable.

### Trait compilation (#100)

`tests/test_traits.py` supplies independent representative construction arithmetic
for Basic Set: Characters, Fourth Edition, 2004 first printing, B101-102 and
B120-121, with the frozen 2007-01-26 errata baseline. It covers level multiplication,
self-control multipliers (6/9/12/15), additive modifiers, the net -80% discount
floor, and final rounding toward higher point cost, including negative totals.
The rules are selected by exact profile; Basic Set modifiers cannot enter Lite.

These three coverage rows remain **partial**: costing is implemented, but runtime
self-control checks (#111), catalog content (#113), and supernatural execution
(#118) remain visible blockers. Disadvantage-specific modifiers and non-percentage
special constructions are unavailable; they require catalog-specific rules in
#118 before activation. No generic hook or manual ruling certifies coverage.

## Tactical geometry (#105)

Tactical geometry (#105): [contracts, provenance and integration boundary](tactical-geometry.md).
Hex movement, facing and geometric LOS have independent fixtures; the rows remain
partial pending source audit and combat/API integration (#115).

## Typed equipment profiles (#101)

`wayfarer.simulation.gurps_equipment` defines strict, immutable, JSON-round-trippable
weapon modes (melee/ranged discriminated union), thrust/swing/fixed d6 damage,
skill references, minimum ST, hands, reach, parry properties, shields/block,
armor locations/DR, price, TL and exact mass. Ranged modes carry Acc, ST-scaled
or fixed ranges (including fractional multipliers), RoF, shots, reload, Bulk,
Rcl and ammunition references. Impossible ranges, damage bases, duplicate modes,
and missing ammunition references fail schema validation. `EquipmentCatalog.bind`
resolves equipment, skill and source references against supplied pinned packages;
missing skills never become invented definitions. These references do not require
an actor to have purchased the skill just to equip a weapon.

The declared `LITE_EQUIPMENT` sample contains a broadsword (two modes) and leather
armor. Numeric expectations reference Lite August 2004, Rev. 07/12/04, pp. 19–20;
no prose is bundled. The official artifact could not be retrieved during this
change, so the source audit remains outstanding and the equipment capability
rows deliberately remain **partial**. The sample does not claim catalog coverage.
Basic equipment catalog/durability coverage remains owned by #114.

`inventory_spec()` adapts data to the existing `ResourceEngine`: **one integer
weight unit is 0.001 lb**, including owner/container capacities. `inventory_load`
requires exact catalog specs and a matching statistics profile, validates the
resource state, and recomputes pounds, encumbrance, Move and Dodge from existing
owned instances. Equipped items count once; transfer and retry use existing CAS
and command receipts. Overloaded characters return an explicit absent band/Move/
Dodge rather than an invented sixth band. The adapter is internal and opt-in;
prototype weights, package digests, saved campaigns and frozen v1 are unchanged.
Do not pass this adapter's units to the v1 integer-gram projection.

`tests/test_gurps_equipment.py` provides numeric sample expectations, strict-schema
and reference failures, ranged round trips, and property-based equip/transfer
conservation with stale-revision and idempotency checks. Combat damage, ST-use
penalties, hand occupancy beyond the inventory slot, active defenses, hit-location
resolution and ammunition consumption in attacks remain with #102, #103, #106
and #107; these data structures do not authorize those unverified mechanics.

## Skill compilation (#98)

`character.skills.SkillCompiler` runs inside the existing `CharacterCompiler` and
`ActionEngine`. Its typed `RuleDefinition.skill` metadata is included in package
digests. No draft or action can supply a difficulty, default, prerequisite, or cap.
The prototype four-skill dispatch and point restrictions are unchanged, including
its package digest and build revision regression cases. A GURPS compiler rejects
prototype-only skill definitions and missing skill metadata.

Package 0.3.0 / profile version 3 adds representative skills to Lite and Characters.
Version 2 remains registered with its original 0.2.0 pins and definitions. Existing
campaigns do not acquire new definitions; the established explicit migration,
approval, command receipt, and CAS path remains the way to switch profiles. Neither
full GURPS profile is yet selectable. Frozen v1 transport schemas are unchanged.

Implemented mechanics and evidence:

- Easy, Average, Hard, and Basic-only Very Hard progression, including partial
  investment and investment above 16 points; exact integer arithmetic (Lite 13,
  B170). Primary attributes, Will, and Per are typed controlling attributes.
- Explicit attribute and skill defaults, strongest eligible default selection,
  the attribute default ceiling of 20, and Basic point-equivalent credit when
  improving a skill default (Lite 14, B173). Untrained defaults cannot serve as
  another default's source. Trained dependency chains resolve in dependency order.
- Distinct required specialties and IQ/Hard or IQ/Very Hard optional specialties,
  with the easier cost curve and general/specialized defaults (B169). Only named,
  pinned specialties exist; missing specialties never fall back to a generic ID.
- Trained minimum-level prerequisites, Average and Hard techniques, a two-point
  first improvement for Hard techniques, and parent-relative maximum levels
  (B169, B229-232). Technique purchases without a trained parent, overspending
  beyond a cap, and bonuses that breach a cap are rejected.
- Recompilation after attribute or point changes; permanent skill effects propagate
  through defaults and technique parents. Actions recompute skill targets with
  equipment effects and allow valid unpurchased defaults, while preserving existing
  visibility, approval, and command boundaries.

The independent numeric ledger in `tests/fixtures/gurps/conformance.json` is executed
by `tests/test_skills.py`; additional tests cover invalid prerequisite graphs,
unsupported definitions, prototype stability, package pin integrity, and actual
action resolution. Capability status records this bounded mechanics evidence,
not a completed skill catalog or full Basic Set certification.

Catalog boundaries remain explicit under #112: only representative definitions
are included; no full skill list, TL/familiarity catalog, cinematic skill rules,
wildcard skills, or arbitrary LLM-authored specialties are supported. Reciprocal
and cyclic declared defaults are rejected; they must be expanded into a reviewed
acyclic selection before use, not silently resolved by dictionary order. Optional
specialty reverse defaults use native trained levels to prevent feeding a default
back into itself. Combat effects of techniques belong to #103; compiling their
levels does not implement grappling or kicking actions.

Sources retain the baseline edition/printing/errata above, with page references
in each spec and fixture. The publisher pages were inaccessible during #98, so
these numeric cases do not remove the existing exact-source-artifact review merge
gate. No source prose is bundled.

## Mundane skill inventory (#112)

`rules.mundane_skills` is a dedicated, versioned candidate package and item-level
inventory for the Basic Set skill chapter. It records skill families, aliases,
weapon classes and representative expanded specialties/techniques with page
references, controlling attributes, difficulty and numeric attribute defaults.
Cinematic/supernatural entries have a separate exclusion inventory. The inspected
Characters printing is third; the frozen first-printing delta audit is pending.

`python -m scripts.audit_mundane_skills` emits coverage directly from this
inventory, including each entry's explicit blockers and owning issues. Conditional
skill defaults, required specialties, TL context and prerequisites are identified
as blockers rather than discarded or guessed. Missing entries reject. Candidate
package definitions are unsupported and cannot activate; they do not mutate
existing pinned packages or advertise that a generic check implements a profession,
medical procedure, vehicle or weapon. Independent tests sample difficulty classes,
Will-based targets, optional/required specialties, techniques, reference integrity
and unavailable/unknown IDs. Full specialty expansion and runtime availability
remain visible item-level blockers under #112 and the indicated mechanics owners.

The candidate `0.2.0` audit validates its inventory and exclusions with strict
typed records. Defaults, prerequisites and specialty parents must reference
accounted-for entries; required-specialty and TL flags survive into the report.
The inventory now contains 257 records, including six Mathematics specialties,
with 238 structured definitions and 28 exclusions. Twelve entries have complete
unconditional default lists; conditional defaults remain explicitly blocked.
Representative fallback definitions are normalized to unsupported just like newly
indexed entries. These checks improve data integrity without making blocked
skills playable or changing existing rule-package pins.

## Provisional social procedures (#111)

`rules.gurps_social` implements reaction bands and typed status/reputation/
appearance modifiers, influence contests with Diplomacy fallback and Sex Appeal
outcomes, self-control from catalog-validated TraitOptions, and Basic-only fright
checks with the Rule of 14. References are reconstructed from model knowledge
under the owner's explicit authorization: Lite 3-4/10/24, B120-121, B359-362,
B494-495, using the frozen 2004/2007-errata baseline; source audit is pending.

`simulation.social` stores results and private traces in the existing resource
receipt/event ledger for atomic checkpoint commits. Duplicate command IDs replay;
a second command cannot reroll the same subject/trigger. NPC trigger evidence is
checked against the subject's knowledge. The explicit public projection excludes
all roll targets, hidden modifier values and source IDs. No player choice is
modified by a social outcome. Explicit server-authored NPC disclosures can teach
the initiating actor configured facts already known to the NPC; ordinary rolls
do not reveal other facts or change NPC beliefs.

Reaction/influence/fright coverage remains **partial**, and runtime self-control
is partial: these are server-only procedures, with full NPC play dispatch and
timed consequence execution in #137. The complete numeric fright table is
represented by typed FrightEffect records: durations, recovery attributes and
intervals, HP/FP losses, aftermath penalties, permanent attribute losses and
explicit GM trait/panic choices. Each row has executable tests. Table effects
are persisted in the private receipt; applying timed effects to live characters
remains an explicit integration blocker in #137. Coverage does not claim that
recording an effect already executes it. These blockers remain visible for #122.

`orchestration.social.SocialService` binds a trusted trigger resolver and commits
the resource receipt and bounded NPC disclosure together through the existing
campaign transaction. It checks campaign GM membership and the exact compiler
profile, and rejects reaction/influence dispatch against player-controlled
subjects. Persisted retries do not re-run the resolver or recheck changed world
knowledge. Colon-bearing trigger identities cannot alias, and legacy receipts
remain readable. Player projections and event streams omit private traces.
Fright dispatch rejects before rolling until timed consequence integration is
available; its pure table resolver remains separately testable. Independent
SQLite restart, stale command, failed disclosure, authority, and projection tests
cover this boundary. The profile registry remains gated pending certification.

## Provisional implementation policy (2026-09-07)

The project owner explicitly authorized implementation from model knowledge while
source artifacts are unavailable. This permits engineering PRs to merge with
passing tests; it does not certify source accuracy. References below identify the
intended edition and pages, not an assertion that those pages were inspected.
The independent source audit remains a certification task, not a merge gate for
these explicitly authorized provisional implementations.

## Torso injury reducer (#102)

`simulation.injury.apply_injury` applies server-owned wounds and ordered injury
turns to the existing ResourceState/Pool checkpoint. Pool.injury opts in to an
exact GURPS profile; signed HP are rejected on prototype and FP pools. Resource
receipts persist the check traces and make repeated/deferred hit commits safe
across JSON reload. The caller persists the entire checkpoint with commit_turn
and CAS. Prototype attack dispatch rejects profile HP rather than clamping it.

Hand-entered tests cover DR penetration, rational wounding factors with floor and
minimum penetrating injury, negative HP, each crossed death threshold, automatic
death at -5 HP, mortal wounds, shock, major wounds, knockdown, consciousness at
turn start, and stun recovery after Do Nothing. Rebuilds preserve the full injury
deficit, and ordinary healing retains injury status. Low-HP Move/Dodge uses
ceiling division. Turn ordering rejects repeated phases with new command IDs.

Coverage remains **partial** pending source verification and complete maneuver
timing (#104). The profile-selected melee adapter now persists deferred injury,
turn-start consciousness and end-turn stun recovery through the existing CAS
boundary; see [melee integration](gurps-melee.md) for #102/#103 evidence and
remaining Basic critical consequences in #146. Recovery from mortal wounds and elapsed
medical checks belong to #109; location effects to #107. Fatigue damage rejects
until #109; corrosion's persistent armor destruction remains unavailable under
#114. No generic damage multiplier implements those missing runtime effects.
The intended source is Lite August 2004 pp. 28-30 and Basic Set Campaigns first
printing B378-381, B419-423 plus the selected 2007-01-26 errata; source audit pending.

## Character workshop integration (#116)

The additive workshop contract in `contracts/workshop/v1` describes profile
previews, catalog metadata and advancement input separately from frozen gameplay
v1. Generated TypeScript types drive skill difficulty/default/specialty/technique
and typed trait parameter/self-control/modifier controls. Prices and derived
values come from CharacterCompiler; no client formula decides point totals.

Profile previews are read-only. Selecting another profile cannot switch a saved
campaign or bypass its explicit migration/capability gate. Save and generation
operate on the active campaign compiler. Approval is separate from legality;
unsaved edits disable activation, and saved edits invalidate the prior approval.
The existing CAS/build revision/earned-points ledger handles advancement previews
and purchases. Setup activation preserves depleted HP/FP rather than healing via
rebuild. Full GURPS campaign activation still requires its outstanding mechanics;
the workshop exposes those blockers and never silently substitutes prototype rules.

The G4 follow-up adds explicit draft submission and a typed GM review queue in
the authenticated lobby. Unsubmitted edits remain private, editing invalidates
submission/approval, and submitted drafts require explicit GM approval before
activation. Review does not grant control of another player's character. GM
point awards and player purchases use the existing advancement ledger and CAS.
HTTP and live browser journeys cover submit, stale approval rejection, resubmit,
approve, activate, award and advance; committed command retries do not repeat
awards or purchases. Browser viewport/batch isolation retains all evidence while
keeping production rate limits unchanged. Full profile certification remains
separate from this generic workshop integration.
