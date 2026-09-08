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

Construction and audit coverage came first; two effects now execute.

## Executable effects and the profile that declares them

An entry is available only when an authoritative service applies its effect, a
registered profile declares the effect as a runtime hook, and independent tests
cover it. `IMPLEMENTED_EFFECTS` names that set: `trait.off_hand` and
`trait.combat_reflexes`. Everything else stays `unsupported`, so the compiler
refuses the purchase rather than selling a trait that does nothing.

Basic profile v7 / Characters package 0.7.0 publishes exactly those two entries,
Ambidexterity [5] and Combat Reflexes [15], additively. Packages 0.2.0-0.6.0 and
profiles v2-v6 keep their bytes and still refuse both traits, so no saved campaign
changes and switching remains an explicit migration. The candidate package keeps
all 56 construction records and gains no hooks; a catalog alone never activates a
trait. `RegisteredProfile.trait_runtime_hooks` carries the declaration into its
digest, and `runtime_engine` and the profile preview pass it to the compiler, so
a profile that omits an effect rejects the purchase with `trait.runtime_unavailable`.

Executed scope, and nothing wider:

- **Ambidexterity (B39)** removes the off-hand penalty from an All-Out Attack
  (Double): both attacks use full skill instead of the declared hand's -4.
- **Combat Reflexes (B43)** adds +1 to every active defense — Dodge, Parry and
  Block, armed and bare-handed alike — and +2 to a Fright Check. The Fright bonus
  is read from the approved build inside the trusted dispatcher, never from the
  caller's social context, which supplies only Will, HT and the situation. Its
  Fast-Draw, initiative, surprise and mental-stun effects are **not** implemented
  and remain part of this issue's open coverage.

No other profile or package pin is changed.

The audit reports source/runtime blockers and excluded construction variants; unimplemented modifiers and background interactions are not certified
by correct prices. First-printing delta verification and complete Basic Set
coverage remain open. The selected numeric references use Characters Fourth
Edition, third printing; baseline certification is a separate task.

Independent tests cover trait costs, self-control multipliers, background
identities, exclusions, template totals and failure cases. For the two executable
entries they also cover the effects themselves and the gate around them:
`tests/test_mundane_traits.py` checks that no trait activates through a catalog
that declares no hook and that v7 accepts only its declared two;
`tests/test_gurps_melee.py` and `tests/test_unarmed.py` check the +1 on Dodge,
Parry, Block and an unarmed Parry; `tests/test_maneuver_followups.py` checks the
off-hand attack at full skill against the same fixture's -4;
`tests/test_fright.py` and `tests/test_fright_runtime.py` check the +2 at the roll
and through the dispatcher, with the unmodified roll failing on the same dice.
The numeric costs and both bonuses are entered from Characters third printing;
the first-printing/errata delta stays an open audit item for #191, and every
entry keeps its `first-printing-delta-audit` blocker. The pre-publication
combined backend suite passed 848 tests with 14 database-dependent skips;
strict typing, formatting and lint checks passed.
