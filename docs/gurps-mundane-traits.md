# Mundane trait and template inventory (#113)

The separate candidate package contains 56 selected mundane/background records
for the default finite campaign vocabulary. They include advantages,
disadvantages, perks, quirks, wealth, status, rank, additional spoken/written
languages, cultural familiarity and five social relationship constructions.
Each record identifies numeric costs, exclusions, effect dependencies, source
pages and any manual obligations. Changing campaign identity vocabulary changes
the package digest; an identity never supplies a formula or arbitrary cost.

Five original racial/occupational templates compose purchases through
`character.templates.TemplateCatalog` and the existing `CharacterCompiler`.
Selections, inclusion cycles, duplicate purchases, unresolved references and
taboo traits reject. Templates and their compiler package pins have a digest;
the client cannot override totals or manufacture activation approvals.

This is construction and audit coverage, not a runtime trait implementation.
All candidate effects remain unsupported, and no existing profile or package pin
is changed. The audit reports source/runtime blockers and excluded construction
variants; unimplemented modifiers and background interactions are not certified
by correct prices. First-printing delta verification and complete Basic Set
coverage remain open. The selected numeric references use Characters Fourth
Edition, third printing; baseline certification is a separate task.

Independent tests cover trait costs, self-control multipliers, background
identities, exclusions, template totals and failure cases. The pre-publication
combined backend suite passed 848 tests with 14 database-dependent skips;
strict typing, formatting and lint checks passed.
