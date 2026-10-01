# Construction conformance audit (#834)

Frozen scope at `e77bd5cc58c53262ace4289e658fea9638530630`: the six existing
capability rows below. This audit checks their named construction branches;
it does not certify every catalog skill, template, trait, or campaign policy.
Source inspection used the selected Characters third-printing PDF whose SHA-256
is `872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`.
No capability status or release-gate declaration changes in this PR.

All executable IDs below belong to `tests/test_conformance_construction.py`.
Expected numbers are entered from the selected source. Test input definitions
for the generic default rule are deliberately separate from catalog-coverage
claims. Registered Karate, Judo, Kicking (Karate), and Arm Lock (Judo) definitions
exercise the actual skill compiler.

| Frozen capability row | Source and supported construction | Actual consumer | Executable case ID |
| --- | --- | --- | --- |
| `gurps.character.primary_attributes` | B14: ST/DX/IQ/HT absolute purchases, including a reduction | `CharacterCompiler.compile` | `test_purchased_statistics_compose_in_actual_build` |
| `gurps.character.secondary_characteristics` | B15-17: independently purchased HP/Will/Per/FP/Speed/Move; ST11 BL rounding; damage table | `CharacterCompiler.compile` | `test_purchased_statistics_compose_in_actual_build` |
| `gurps.character.secondary_characteristics` | B17: inclusive load limits and floor of fractional Move; minimum Move/Dodge 1 | `encumbrance`, `encumbered_move`, `encumbered_dodge` (consumed by `inventory_load`) | `test_encumbrance_inclusive_edges`, `test_encumbrance_never_reduces_dodge_below_one` |
| `gurps.character.skill_difficulty` | B170: all four skill-cost columns, 1/2/4/8/12/16 points | `relative_level`, called by `SkillCompiler.compile` | `test_skill_cost_table_independent_columns` |
| `gurps.character.skill_defaults` | B173: attribute defaults cap at 20 before modifiers | `SkillCompiler.compile` | `test_attribute_default_uses_twenty_ceiling` |
| `gurps.character.techniques` | B230/B232: Average/Hard point progressions and parent-relative level | `SkillCompiler.compile` | `test_average_technique_cost`, `test_hard_technique_cost_and_parent_cap` |
| `gurps.character.specialties` | B169-170: optional specialty defaults and required specialties remain covered by existing independent compiler cases | `SkillCompiler.compile` | `tests/test_skills.py::test_compiler_uses_effective_attributes_points_and_distinct_specialties` |

The new minimum-Dodge case exposed the missing B17 floor in the existing helper.
The focused repair clamps its result to 1. This does not change the declared
immobility boundary of Basic Move 0, and does not claim compliance for physiology,
combat penalties, or inherited creature traits owned by other audit issues.

Existing tests continue to own permission advisories, unlisted ST damage rows,
Size Modifier discounts, effect propagation, reciprocal defaults, specialty
availability, and activation/resource-pool preservation. This numerical audit
adds independent boundary evidence without promoting those entire families.
