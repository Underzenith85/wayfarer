# Basic Set mundane skill inventory (#112)

`rules/mundane_skills` is the item-level accounting for the Basic Set skill
chapter (B168–B233). **Accounting for an entry never makes a skill playable.**
Every row keeps an explicit blocker and no row is available through this
inventory. A row becomes executable only when a runtime module binds it to a
service that already resolves it and a new package pin carries that definition;
the candidate package here stays separate, immutable and hookless, and no
existing campaign profile, package pin or saved character changes.
See [Ranged combat procedures](#ranged-combat-procedures-344) for the first
group bound this way.

## Source boundary

| Observed source | References | Reconciliation |
| --- | --- | --- |
| Characters, Fourth Edition, third printing | B168–B233 skill chapter and index | The frozen baseline is `gurps-4e-2004-first-printing+errata-2007-01-26`. Every row carries the `first-printing-delta-audit` blocker until #191 reconciles the printings. |

Records hold identifiers, page references, controlling attribute, difficulty,
numeric defaults, prerequisites and specialty metadata. No rulebook prose is
bundled. Values not confirmed against the source stay absent and blocked rather
than reconstructed into a runnable roll.

## Accounting matrix

`python -m scripts.audit_mundane_skills` emits this matrix from the inventory
itself; the counts below are the current report, not a separate transcription.

| Accounting group | Rows | Coverage decision |
| --- | ---: | --- |
| Structured candidate definitions | 245 | Attribute, difficulty and recorded defaults exist. 233 are normalized to `unsupported`; the 12 rows with a bound runtime procedure (#344) are reported as `implemented` and stay blocked. |
| Listing-only rows | 19 | Family, variable-scope and unexpanded entries whose mechanics are not recorded at all. They carry `metadata-audit` and cannot be mistaken for a definition. |
| Transferred exclusions | 28 | Cinematic and supernatural skills owned by #119 with named follow-ups #242/#243 and #191. Exclusion from this inventory is not exclusion from the Basic Set. |
| **Total accounted** | **292** | **No available row.** |

Each row is also classified by the structure it actually records, so fixtures
sample every class instead of the common shape only. Every class below must stay
populated; an unsampled class fails `validate_inventory`.

| Structural class | Rows | Meaning |
| --- | ---: | --- |
| `attribute-default` | 184 | At least one numeric attribute default. |
| `skill-default` | 18 | At least one default from another accounted-for skill. |
| `no-default` | 59 | No default is recorded; a missing default is not an implied attribute default. |
| `technology-level` | 76 | Requires TL context that this inventory does not supply. |
| `unexpanded-specialty` | 57 | A required specialty exists in the source and is not expanded here. |
| `listing-only` | 19 | No recorded mechanics. |
| `required-specialty` | 14 | Expanded distinct specialty with no cross-specialty inference. |
| `prerequisite` | 3 | Needs another trained skill. |
| `technique` | 2 | Parent-relative technique, not an independent skill. |
| `optional-specialty` | 1 | Optional specialty bound to its unspecialized parent. |

## Ownership and remaining blockers

Item blockers name the issue that must resolve them, and those numbers reach the
certification report directly: `source_audit` consumes each row with its own
blockers and `unsupported`/`listing-only` state instead of one family status.

| Blocker | Rows | Owner |
| --- | ---: | --- |
| `first-printing-delta-audit` | 264 | #191 printing/errata reconciliation |
| `runtime-procedure` | 215 | #103, #109, #110, #111, #354, #355, #357, #359, #360, #361 where named; otherwise unassigned |
| `conditional-or-skill-defaults` | 122 | #362 for the ranged rows; otherwise unassigned |
| `technology-level-context` | 76 | #355, #357, #359 for the ranged rows; otherwise unassigned |
| `specialty-expansion` | 56 | #355, #357, #359, #361 for the ranged rows; otherwise unassigned |
| `metadata-audit` | 19 | Unassigned |
| `weapon-default-audit` / `combat-procedure` | 18 | #103 |
| `family-specialty-expansion` | 15 | Unassigned |
| `prerequisite-procedure` | 7 | Unassigned |
| `variable-family-metadata` | 4 | Unassigned |

209 of 264 rows currently name no mechanics owner beyond this audit. The report
publishes that as `runtime_owner_unassigned`, so the gap is visible to #122
rather than implied by a family-level "partial". Naming those owners requires
dependency-linked follow-up issues and remains outstanding.

Exclusions are validated against the catalog that took them: each excluded skill
must exist in the #119 inventory with the same page and the same follow-up
issues. Drift there fails this audit instead of dropping the skill.

## Ranged combat procedures (#344)

`rules/mundane_skills/ranged.py` is the only place a listed ranged combat row
becomes executable. A row is implemented when the module binds it to the ranged
dispatch that already resolves it (`orchestration/gurps_ranged`), declares the
exact weapon modes it governs and names a registered capability
(`gurps.combat.ranged_weapon_skills`, #344). Naming a procedure never
implements one, and neither does a generic target calculation: a weapon whose
mode falls outside its skill's class is refused before dice by
`simulation.gurps_equipment.require_skill_procedure`, which runs when an
equipment catalog is built and again when a weapon mode is selected in play.

| Row | Reference | State |
| --- | --- | --- |
| `skill:bow` | B182, DX/A, DX-5 | Implemented. Two-handed launcher with a pinned missile, one shot, no recoil ladder. |
| `skill:crossbow` | B186, DX/E, DX-4 | Implemented. Launcher with a pinned missile. |
| `skill:sling` | B221, DX/H, DX-6 | Implemented. Launcher with a pinned missile. |
| `skill:blowpipe` | B180, DX/H, DX-6 | Implemented. Launcher with a pinned missile. Poisoned ammunition is an ammunition mechanic, not part of this skill. |
| `skill:thrown-weapon` | B226, DX/E, DX-4 | Family expanded into seven concrete specialties (Axe/Mace, Dart, Harpoon, Knife, Shuriken, Spear, Stick). The family itself is never dispatched. |
| `skill:thrown-weapon-*` | B226, DX/E, DX-4 | Implemented. The projectile is the item; it leaves active inventory and is retained in `expended_items`. |
| `skill:bolas` | B181, DX/A | Transferred to #354; the outcome is a persisted entangled condition, not injury. |
| `skill:net` | B211, DX/H | Transferred to #354 and #362. |
| `skill:spear-thrower` | B222, DX/A, DX-5 | Transferred to #360 and #362; a launcher that modifies a projectile it does not consume. |
| `skill:guns` | B198, DX/E, DX-4 | Transferred to #355; needs TL context and pinned firearm specialties. |
| `skill:beam-weapons` | B179, DX/E, DX-4 | Transferred to #355. |
| `skill:artillery` | B178, IQ/A, IQ-5 | Transferred to #357; mounted or crew-served, and IQ-based. |
| `skill:gunner` | B198, DX/E, DX-4 | Transferred to #357. |
| `skill:liquid-projector` | B205, DX/E, DX-4 | Transferred to #359; needs stream and spray state the dispatch does not have. |
| `skill:innate-attack` | B201, DX/E, DX-4 | Transferred to #361. The Projectile specialty is already dispatched by the opt-in spell adapter under its own pin; reconciling it here is an explicit migration. |

The bindings may only resolve or keep the blockers the inventory recorded, and
their numbers must be the recorded ones: `inventory()` raises on either drift.
A transferred row must name an owner, so a blocker cannot be quietly reworded
into silence. Every row still carries `first-printing-delta-audit`, so an
implemented procedure is reported as `implemented` and stays unavailable.

The definitions live in a new pin, `package:...@0.7.0` with profile version 7
(`rules/profiles.py`). Existing v2-v6 campaign pins resolve byte-for-byte
unchanged; switching a campaign still uses the existing explicit migration.
Evidence is in `tests/test_ranged_skills.py`.

## Runtime contract

`require_available` rejects every unknown identifier and every blocked row, and
cleared blockers still cannot activate an unsupported definition. The candidate
package `package:gurps-mundane-skill-candidates` is separate and immutable; its
definitions carry no hooks, so scenario, character and LLM validators cannot
turn an accounted-for row into a mechanic. `tests/test_mundane_skills.py` fixes
the structural classes, numeric default alternatives, specialty and prerequisite
identities, owner propagation and the exclusion transfer independently of the
audit report that consumes them.
