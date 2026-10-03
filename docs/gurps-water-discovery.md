# Seek Water findings and remembered sources

Characters third printing B253 gives the caster the direction, distance and
general nature of the nearest significant water source, and permits known
sources to be excluded before a later cast. A successful private Water cast
already records that discovery; the host now returns its finding to the caster
and includes it in the current controller's campaign and stream projections.
The private `WaterSpellResult` adds only direction, distance and nature. Public
`SpellResult`, spell command/effect schemas and persisted spell events are
unchanged. Finding projections omit internal source identities, material plans,
channel records and other actors' discoveries.

The existing successful discovery record is also source-bound knowledge for
that actor's later predeclared exclusions. This does not reveal the hidden world
entity, its other facts, or the source to another actor. The current authored
source must still exist. Ordinary failed casts create no finding or knowledge.
An empty search returns an explicit finding with no direction, distance or
nature and grants no source identity. Successful findings survive restart and
exact retry without additional magic, material changes or entropy.

`tests/test_water_discovery.py` uses an approved caster finding a hidden source
at offset (7, 2), independently expects direction (7, 2), distance 8 and its
authored nature, then successfully excludes it in a subsequent cast. It checks
another player's and spectator's projections and retry authority, failed-cast
nonadmission, preserved world secrecy, restart, ordinary event folding and full
seeded command reexecution on SQLite and PostgreSQL. Current actor control and
trusted GM authority remain enforced by the canonical command pipeline.

This document describes the Seek information consequence within the merged
[four-spell baseline](gurps-spell-water-effects.md). Receiver mixing, full-source
purification and admitted one-gallon mist now have companion execution evidence.
The baseline document lists the genuinely unsupported contracts separately; this
finding feature does not certify other water-college spells or physical variants.

Remaining Water material and geometry contracts are tracked in
[issue #981](https://github.com/Underzenith85/wayfarer/issues/981).
