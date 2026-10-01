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

The corresponding exact assigned section-ledger rows are frozen below; the
capability table above supplies consumers and source-derived executable cases:

- `section:characters:b014:basic-attributes`: actual purchased-statistics build.
- `section:characters:b015:secondary-characteristics`: actual purchased-statistics build and encumbrance cases.
- `section:characters:b016:damage-table`: ST11 table row in the purchased-statistics build; existing `test_statistics.py` source-entered damage cases retain other rows.
- `section:characters:b017:basic-lift-and-encumbrance-table`: inclusive load-edge matrix and minimum Move/Dodge.
- `section:characters:b167:controlling-attribute`: four compiled skill-cost columns at DX10.
- `section:characters:b168:difficulty-level`: four compiled skill-cost columns.
- `section:characters:b169:prerequisites`: registered Judo/Karate technique parent purchases, existing `test_skills.py::test_prerequisite_cannot_be_met_by_a_default_or_insufficient_training`.
- `section:characters:b169:specialties`: existing distinct-specialty compiler case named above.
- `section:characters:b170:buying-skills`: exact point allocations in compiled skill-cost columns.
- `section:characters:b170:skills-cost-table`: all four compiled columns and +4-point progression.
- `section:characters:b171:relative-skill-level`: compiled skill-cost columns at controlling attribute10.
- `section:characters:b173:skill-defaults-using-skills-you-don-t-know`: attribute-default ceiling; existing `test_skills.py::test_default_chains_require_training_at_every_link` and `test_reciprocal_defaults_resolve_from_purchased_levels_without_a_cycle` preserve trained-source and no-double-default branches.

The techniques matrix additionally checks the B230 cost table through the
registered B230 Arm Lock and B232 Kicking constructions. B10-13 creation guidance,
B18-34 identity/background/wealth/appearance, and other nonnumerical narrative
sections are not assigned to this numerical matrix. Source review does not turn
reference-only guidance into an executable operation. Technology and familiarity
construction are retained under their existing source-specific consumers; this
matrix does not silently extend ordinary defaults to conditional TL/familiarity
branches.
