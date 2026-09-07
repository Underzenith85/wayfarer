# Executable Basic Set magic

Issue #171 connects the four representative spells to approved characters and
existing world, combat, injury, hazard and campaign-ledger services. It does not
make the full Basic Set magic catalog executable. `gurps.magic.spellcasting`
remains **partial**; the exhaustive catalog and frozen-source audit remain #119.

## Opt-in configuration

Profile version 5 adds Characters package `0.5.0`. Its spell prerequisites require
one purchased point, correcting the earlier reconstructed skill-12 requirement.
Profile versions 1–4 and their package pins are retained. Loading another profile
or changing a campaign's `SpellRules` requires the existing explicit migration.

`SpellRules.execution_version=2` selects completion at the end of the final
Concentrate maneuver, critical backfires and very-high mana. Existing execution
version 1 retains its saved timing. No command was added to frozen play v1.
The additive authoring and scenario schemas include the new configuration.

Player commands select an authored channel; skill, Magery, IQ, Will, HT,
resistance, placements and energy limits come from the saved approved builds.
Light's falloff is authored with `light_radius` and `light_penalty` (defaults: two
yards and -3). These are scene illumination conventions, not a claim that B249
assigns a candle a two-yard radius. B249 describes candle-sized light. Hex
positions use the encounter's explicit axial geometry; legacy square maps are
never silently interpreted as hexes.

## Source review and independent examples

The implementation review used Characters, fourth edition, **third printing,
February 2008**, and Campaigns, fourth edition, **fourth printing**. This is not
certification of the repository's separate first-printing/2007-errata baseline.
No source text is included in the fixtures. Numeric expectations were entered
independently in `test_spell_energy.py`, `test_spell_backfires.py` and
`test_spell_execution.py`.

| Reference | Reviewed behavior and executable evidence |
| --- | --- |
| B66–67, B235 | Magery cost/skill bonus, purchased spell prerequisites, normal/high mana permissions, and low mana's -5 for skill, energy discounts and ritual time. |
| B235, B237 | FP/HP contributions, -1 casting skill per HP, free critical successes, ordinary-failure cost, and unconsciousness without spending a fatal HP payment. |
| B235–236 | Very-high mana refunds on the next caster turn, ordinary failure becoming critical, and a separate authored spectacular-disaster decision. Refunds cannot bank credit or restore unrelated fatigue debts. |
| B236 | Actual injury, IQ-based mental stun, observable noise/shadows/illusory appearance, weekly spell-memory checks, and durable GM interpretation of contextual results. |
| B237–238 | Completion within the final Concentrate turn, one energy to cancel a running timed spell, conscious maintenance, -3 for a manipulated spell and -1 for other active spells. Lost manipulation freezes the effect; a critical distraction ends it. |
| B241, B247 | Up to three consecutive Fireball casting/expansion seconds, held-missile disposal, Will after injury, self-impact on losing control, Wait release, defense and burning injury, and nonpositive-HP consciousness checks. |
| B246, B400, B433 | Stationary Create Fire, axial-hex radius, full-turn and transient crossing exposure, and large-area DR. A crossing that ends outside the fire still causes exposure. |
| B249–251 | Light moves with Concentrate at Move 5; Daze prevents action/defense and ends on injury or successful resistance to another spell. |
| B382, B556–557 | Fireball uses ranged critical-failure thresholds and supported body critical-hit consequences. Context-dependent physical ranged critical misses retain #173's explicit pause. |

## Context-dependent critical results

Automatic table rows execute immediately. Other rows persist a private pending
record and pause subsequent play. `SpellBackfireService` accepts only a campaign
GM selecting an existing `BackfireAlternative` from the saved rules. A player
cannot supply new targets, damage expressions or an outcome in the command.

Alternatives implement retargeting, reversal, an approved reserve combatant's
summoning, or an authored spectacular mishap. Summoning requires an existing
approved actor and a valid unoccupied placement; it cannot invent a character.
The GM controls that combatant through ordinary combat procedures. A reversed
Fireball's damage expression is explicitly authored because the critical table
does not define it. Improvised damage is capped before a death threshold.

The table's good-intent exception can waive only a normal demon result. An
inappropriate result may use an authored reroll alternative; a spectacular
very-high-mana disaster cannot be downgraded to a normal reroll. Undefined
alternatives fail closed. Illusory success exposes only its apparent outcome to
the player; the real result remains in the private ledger.

## Persistence and limits

Costs, table dice, selected consequences, injuries, refunds and observable facts
commit through the existing revision/CAS transaction. Concurrent retries use the
same saved result. SQLite and PostgreSQL exercise cast and GM-decision retries.
Recovery rolls cannot be banked by advancing beyond their deadline.

These seams do not expose a new HTTP spell API. Learning-only spells remain
learning-only. Ceremonial casting, arbitrary spell creation, autonomous demon
AI, moving Shape Fire, and the remaining #173 ranged-critical variants are not
silently approximated. They remain outside this representative execution set.
