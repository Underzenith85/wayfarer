# Great Haste: larger ongoing before-action Steps

A fresh private ongoing carrier admits a canonical Step during an existing Great
Haste cast when approved spell base skill, reduced only by low mana, is at least20.
The source interpretation combines B237's absence of ritual at20+ with the normal
Concentrate Step allowance in B366/B368. B237 does not explicitly describe a larger
Step; this interpretation does not broaden the15–19 one-yard-per-second ritual.
All selected movement precedes the casting action and final roll. B236 ends the
caster opportunity at that roll, so no trailing movement is admitted.

`OngoingStepCastGreatHaste` and `NamedOngoingStepCastGreatHaste` accept only
`concentrate`, with captured private generations8 and9 respectively. They require
an existing consecutive casting opportunity and the same current source/build,
channel, target and named knowledge. The canonical square/hex mover enforces the
current Move-derived Step allowance, posture, restrictions, path and occupancy.
Movement does not grant an extra opportunity, reset defenses or create a clock.
Existing Wait leases retain their accepted opportunity and distraction witness.

The complete host scenario uses approved base20 and Move11: a two-second Great
Haste cast finishes after a two-yard ongoing Step, pays3FP once, grants an actual
subject maneuver opportunity and expires after10s with the ordinary5FP noncaster
subject cost. A low-mana campaign with explicit skill ceiling30 demonstrates
base24 refusing and25 admitting (ritual base20). The campaign ceiling is configured
through the existing compiler policy; the catalog and production defaults are
unchanged. Normalbase25 completes in one opportunity and cannot manufacture an
ongoing cast.

Tests exercise actual Play/Combat service producers on SQLite and PostgreSQL,
including current geometry, source thresholds, authority/stale refusal, entropy
rollback, failed roll, exact caller retry, projection privacy, real Wait defense
and one Will−3 result, restart/resume/cancel and deterministic reexecution.
The seeded scenario starts from a private-receipt-free canonical genesis, includes
channel admission and encounter creation, and verifies each later command and
state; it does not seed a prepared execution lease. Explicit-dice Will outcomes
are separate from the seeded Wait-decline/resume/cancel scenario.

Recorded generations1–7 preserve their former behavior and serialization. In
particular, recorded4/5 retain the one-yard ongoing cap and6/7 remain initial-only.
The private lease gains no new fields. Public schemas remain unchanged.

Issue980 remains open: after-action/split Steps, cross-encounter targets and the
remaining Apportation carriers are outside this consumer. No helper receipt or
catalog status certifies these extensions.
