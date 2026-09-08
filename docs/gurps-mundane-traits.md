# Mundane trait and template inventory (#113)

The separate candidate package contains 56 selected mundane/background records
for the default finite campaign vocabulary. They include advantages,
disadvantages, perks, quirks, wealth, status, rank, additional spoken/written
languages, cultural familiarity and five social relationship constructions.
Each record identifies numeric costs, exclusions, effect dependencies, source
pages and any manual obligations. Changing campaign identity vocabulary changes
the package digest; an identity never supplies a formula or arbitrary cost.

Six original racial/occupational templates compose purchases through
`character.templates.TemplateCatalog` and the existing `CharacterCompiler`.
One of them, `template:envoy`, is composed only of bound traits, so a campaign
that pins their runtime hooks compiles it into a legal, approvable build.
Selections, inclusion cycles, duplicate purchases, unresolved references and
taboo traits reject. Templates and their compiler package pins have a digest;
the client cannot override totals or manufacture activation approvals.

## Executable effects

Construction cost and executable effect stay separate. `mundane_traits.runtime`
binds an effect only where an existing authoritative service already resolves
it, and a record is implemented only when its effect appears in
`SUPPORTED_HOOKS`. Seven of the 56 records are bound:

| Record | Effect | Executable value | Reference |
| --- | --- | --- | --- |
| Charisma | reaction and influence modifier | +1 per level, to anyone who perceives the character | B41 |
| Voice | reaction modifier | +2, to anyone who hears the character | B97 |
| Status | reaction and influence modifier | +1 per level, where status is recognized | B28 |
| Low Status | reaction and influence modifier | -1 per level, where status is recognized | B28 |
| Bad Temper, Curious, Overconfidence | self-control roll | the existing self-control check on the approved rating | B120-121, B124, B129, B148 |

Reaction and influence modifiers are derived by the server from the initiator's
approved build and its pinned definition: a scenario resolver, template or
player command cannot supply one, and social dispatch rejects a supplied trait
modifier. A trait that is not purchased, whose definition is not implemented, or
whose runtime hook the campaign does not provide contributes nothing. Campaigns
that do not pin the hooks keep compiling these purchases as unavailable, so no
saved campaign changes behavior.

Voice deliberately stays out of influence rolls: its influence-skill bonus is
not implemented, and approximating the missing half would invent a rule. The
remaining 49 records, Rank's reaction bonus, free Status from Wealth or Rank, and
the manual obligations of Honesty, Codes of Honor, Sense of Duty and relationship
traits stay unsupported item-level blockers rather than manual rulings that count
as coverage. Reputation and Appearance have no trait record to bind here at all;
their reaction values come from the authored standing hooks in
[social procedures](gurps-conformance.md#provisional-social-procedures-111)
(#111), and binding them to purchased traits remains an item-level blocker.

## Standing limits

No existing profile or package pin is changed and the candidate package is still
separate from the frozen registry profiles. The audit reports source/runtime
blockers and excluded construction variants; unimplemented modifiers and
background interactions are not certified by correct prices. First-printing
delta verification and complete Basic Set coverage remain open. The selected
numeric references use Characters Fourth Edition, third printing; baseline
certification is a separate task.

Independent tests cover trait costs, self-control multipliers, background
identities, exclusions, template totals, failure cases, each bound modifier
value and audience, and the end-to-end social dispatch of an approved build.
Expected modifier values are written in the tests from the selected source
pages, never read back from the binding table. The combined backend suite
passed 1822 tests and 735 subtests with 20 skips that need an external database
or a Codex login; strict typing, formatting, lint, the quality gates and both
offline contract validators passed.
