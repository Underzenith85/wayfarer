# Basic Set spell construction

Issue #745 replaces the automatic IQ/Hard, empty-prerequisite construction
fallback. College inventories are identities; an entry without explicit reviewed
learning metadata cannot be automatically purchased. The historic seven-spell
profiles retain their pinned construction definitions and purchased-prerequisite
behavior from #863.

Characters third printing B235 and B247–248 and Campaigns fourth printing B480
were reopened for this implementation. Major Healing and Great Healing are
IQ/Very Hard. The healing chain requires purchased spell points, and Major and
Great Healing require Magery 1 and 3 respectively. Lend Energy permits Magery 1
or Empathy. Magery adds to college-spell learning once, using the same mechanism
as historic spells.

Enchant is IQ/Very Hard, requires Magery 2, and requires learned spells from ten
other distinct colleges. Multiple spells from one college count once; Enchant
and other Enchantment spells cannot satisfy that count. Tests use synthetic
college members to exercise this threshold independently; those fixtures do not
certify additional spells.

Plane Shift is IQ/Very Hard and requires Planar Summons for the same plane.
`plane_bindings(plane)` builds explicit catalog identities for that plane;
unspecialized inventory entries cannot silently grant access to every plane.
Planar Summons requires Magery 1 and spells from ten distinct colleges (B247).

`tests/test_spell_construction.py` checks resulting approved skill levels,
rejected purchases, Magery thresholds, distinct college counting, and matching
plane prerequisites. The four owned inventory rows remain partial, with concrete
execution blockers. Construction evidence does not verify spell effects,
maintenance, API exposure, or end-to-end compliance. Shared runtime work belongs
to #746/#747, healing effects to #772, enchantment effects to #785, and Plane Shift
effects to #802. Existing command-boundary tests use explicitly synthetic builds
and are not evidence of production learning or effects.
