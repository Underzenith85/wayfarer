# Reinforcements and Basic-to-hex escalation

`JoinEncounter` has representation-specific placement. Square joins retain the
legacy grid position and facing. Hex joins require an unoccupied, traversable cell
and a legal hex facing, and the arrival must be geometrically visible to at least
one existing participant. Basic joins are GM admissions: the command names the
joining actor and supplies one current, command-scoped adjudication for distance
and both directions of reach, visibility, cover, obstacle and retreat against
every participant.

A participant already in the encounter cannot join again. The joining actor must
be available and conscious, located in the encounter scene, and on a subgroup
clock synchronized with the encounter group. An external subgroup is merged in
the same transaction as membership and initiative. Queued activity, paused
groups, pending defenses, pending unarmed exchanges, interrupted Waits and other
blocked stages fail closed. Admission preserves the current actor and round, so it
does not reset combat or grant an immediate extra turn. It does not grant world
knowledge; normal projection and line-of-sight rules still decide what each
principal can observe.

`migrate_encounter_hex` also accepts an active Basic encounter. The GM supplies a
complete explicit pose for each existing participant and a battlefield at the
encounter's authored scene location. The battlefield is content-addressed, saved
into combat rules and must match the exact configured equipment profile and
reviewed baseline. Active Basic distances, reach, visibility and retreat
feasibility are checked against the proposed geometry. Non-none cover, blocking
obstacles, grounded equipment, unresolved explosions and positioned spell effects
require explicit resolution first.

Escalation changes only the spatial representation. It preserves initiative,
current turn, round, maneuver and defense history, readiness, injuries and other
live encounter state. Pending defense/unarmed/Wait interactions, grips and close
pairs reject rather than being reinterpreted. The existing square-to-hex path is
unchanged. [Hex-to-Basic conversion](mapless-combat.md#hex-to-basic-conversion) is
implemented separately by #329; participant withdrawal remains owned by #330.

Both operations use the normal authorized campaign transaction, compare-and-set
revision, durable receipt, configuration migration and replay paths. Their typed
transport surface is additive in `/api/tactical/v2`; the frozen v1 contract is
unchanged.
