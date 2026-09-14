# Basic Set character and social capability certification (#726)

The authoritative variant-level matrix is
`tests/fixtures/gurps/character-social-capability-certification.json`. Every
variant records an independently expected outcome, selected-source rows, exact
owner inventory, runtime modules, and behavior tests. The source baseline is
*Characters*, third printing (February 2008), plus *Campaigns*, fourth printing
(April 2008).

| Capability | Required variant groups | Executable evidence |
| --- | --- | --- |
| `gurps.character.development` | Adventure awards and gained traits; study, teachers and quick learning; learnable advantages and B294-B296 transformations | `test_character_development.py`, `test_transformations.py` |
| `gurps.character.self_control` | Catalog ratings and cost multipliers; give in, roll and paid success; typed obligations and private replay | `test_traits.py`, `test_social_completion.py`, `test_trait_procedures.py` |
| `gurps.character.traits` | Complete construction ledger; general procedures and templates; mundane, supernatural, obligation and associated-NPC execution | `test_complete_mundane_traits.py`, `test_trait_procedures.py`, trait-family suites, `test_relationship_runtime.py` |
| `gurps.social.fright` | Rule-of-14 check and modifiers; complete B360-B361 consequence table; timed recovery, injury/resource effects and replay | `test_fright_completion.py`, `test_fright_runtime.py`, `test_fright_conditions.py` |
| `gurps.social.influence` | Six B359 skills; automatic and exceptional outcomes; approved-build dispatch and private replay | `test_social_completion.py`, `test_social_dispatch.py`, `test_live_social.py` |
| `gurps.social.reaction` | Reaction bands and standing; purchased trait modifiers and recognition; audience/material outcomes and persisted replay | `test_social_hooks.py`, `test_social_material_outcomes.py`, `test_social_scenario_v2.py` |
| `gurps.social.skill_procedures` | All 26 owned rows; four resolution shapes and all verdicts; specialties, material consequences and fail-closed contexts | `test_social_skills.py`, `test_social_specialties.py`, `test_social_material_outcomes.py` |

`tests/test_character_social_capability_certification.py` fails if any family or
variant disappears, if selected-source identity changes, if a joined source or
inventory row is incomplete, or if a runtime/evidence path is absent. It also
checks the exhaustive 557-row trait ledger, all 38 self-control constructions,
the six development inventory rows, and all 26 social-procedure owner rows.

The referenced suites assert literal numeric and material results, persisted
state transitions, authorization, CAS conflicts, exact-once retries, recorded
entropy, restart, privacy and replay. These seven capability families are verified
for the Basic Set target. The evidence does not certify the separate Lite source,
does not enable Infinite Worlds or optional rules, and does not change the
prerelease engine version.
