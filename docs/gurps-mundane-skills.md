# Basic Set mundane skill inventory (#112)

`rules/mundane_skills` is the item-level accounting for the Basic Set skill
chapter (B168–B233). **It accounts for entries; it does not make any skill
playable.** Every row keeps the shared printing-delta blocker, no row is
available at runtime, and no campaign profile, package pin or saved character
changes because of it. Runtime procedures live in submodules beside the
accounting — `rules/mundane_skills/social.py` for the #345 social group — and
`procedure_registry` reconciles the two so a cleared blocker always has an
executable procedure behind it.

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
| Structured candidate definitions | 238 | Attribute, difficulty and recorded defaults exist, and every definition is normalized to `unsupported`. |
| Rows with a runtime procedure | 19 | The #345 social group. Nine are whole entries; ten keep `runtime-procedure` while a named part is owned elsewhere. |
| Listing-only rows | 19 | Family, variable-scope and unexpanded entries whose mechanics are not recorded at all. They carry `metadata-audit` and cannot be mistaken for a definition. |
| Transferred exclusions | 28 | Cinematic and supernatural skills owned by #119 with named follow-ups #242/#243 and #191. Exclusion from this inventory is not exclusion from the Basic Set. |
| **Total accounted** | **285** | **No available row.** |

Each row is also classified by the structure it actually records, so fixtures
sample every class instead of the common shape only. Every class below must stay
populated; an unsampled class fails `validate_inventory`.

| Structural class | Rows | Meaning |
| --- | ---: | --- |
| `attribute-default` | 177 | At least one numeric attribute default. |
| `skill-default` | 18 | At least one default from another accounted-for skill. |
| `no-default` | 59 | No default is recorded; a missing default is not an implied attribute default. |
| `technology-level` | 76 | Requires TL context that this inventory does not supply. |
| `unexpanded-specialty` | 57 | A required specialty exists in the source and is not expanded here. |
| `listing-only` | 19 | No recorded mechanics. |
| `required-specialty` | 7 | Expanded distinct specialty with no cross-specialty inference. |
| `prerequisite` | 3 | Needs another trained skill. |
| `technique` | 2 | Parent-relative technique, not an independent skill. |
| `optional-specialty` | 1 | Optional specialty bound to its unspecialized parent. |

## Ownership and remaining blockers

Item blockers name the issue that must resolve them, and those numbers reach the
certification report directly: `source_audit` consumes each row with its own
blockers and `unsupported`/`listing-only` state instead of one family status.

| Blocker | Rows | Owner |
| --- | ---: | --- |
| `first-printing-delta-audit` | 257 | #191 printing/errata reconciliation |
| `runtime-procedure` | 211 | #103, #109, #110, #111, #345 where named; otherwise unassigned |
| `technology-level-context` | 76 | #367 for Propaganda; otherwise unassigned |
| `conditional-or-skill-defaults` | 115 | #353 where named; otherwise unassigned |
| `specialty-expansion` | 57 | #366 for the social rows; otherwise unassigned |
| `metadata-audit` | 19 | Unassigned |
| `weapon-default-audit` / `combat-procedure` | 18 | #103 |
| `family-specialty-expansion` | 15 | Unassigned |
| `prerequisite-procedure` | 7 | Unassigned |
| `variable-family-metadata` | 4 | Unassigned |

210 of 257 rows currently name no mechanics owner beyond this audit. The report
publishes that as `runtime_owner_unassigned`, so the gap is visible to #122
rather than implied by a family-level "partial". Naming those owners requires
dependency-linked follow-up issues and remains outstanding.

Exclusions are validated against the catalog that took them: each excluded skill
must exist in the #119 inventory with the same page and the same follow-up
issues. Drift there fails this audit instead of dropping the skill.

## Runtime procedures (#345 social group)

The nineteen social rows carry executable whole-entry procedures. Each row's
`implementation` reflects its own entry rather than the group:

| State | Rows | Meaning |
| --- | ---: | --- |
| `implemented` | 9 | Acting, Diplomacy, Fast-Talk, Gesture, Intimidation, Lip Reading, Politics, Sex Appeal, Streetwise. The whole entry runs. |
| `partial` | 10 | The procedure runs, but a named part of the entry is transferred to an open child and the row keeps `runtime-procedure`. |

Transferred scope is published by `unsupported_scope`, reported as
`transferred_procedure_scope`, and owned by:

| Owner | Rows | Transferred part |
| --- | --- | --- |
| #366 | Fortune-Telling, Savoir-Faire | The required specialties each row needs before it is learnable |
| #367 | Propaganda | The technology level that decides medium, reach and duration |
| #368 | Interrogation | Coercion modifiers and their injury, fatigue and reaction cost |
| #369 | Leadership, Teaching | Group activity and study-time advancement bindings |
| #370 | Carousing, Panhandling, Performance, Public Speaking | Audience reactions, income and material outcomes |

`validate_procedures` rejects a row that clears `runtime-procedure` without a
whole entry behind it, a procedure whose transferred part names no owning issue
on the row it blocks, and a row that records neither a blocker nor a procedure.
The behaviour of each procedure is documented in
[the social runtime](gurps-social-runtime.md).

## Runtime contract

`require_available` rejects every unknown identifier and every blocked row, and
cleared blockers still cannot activate an unsupported definition. The candidate
package `package:gurps-mundane-skill-candidates` is separate and immutable; its
definitions carry no hooks, so scenario, character and LLM validators cannot
turn an accounted-for row into a mechanic. `tests/test_mundane_skills.py` fixes
the structural classes, numeric default alternatives, specialty and prerequisite
identities, owner propagation and the exclusion transfer independently of the
audit report that consumes them.
