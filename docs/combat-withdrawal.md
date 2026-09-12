# Individual combat withdrawal

`withdraw_encounter` is a persisted combat lifecycle command, distinct from the
Basic relative `withdraw` movement direction and the GURPS Retreat defense. It
does not move an actor. The actor must first complete a normal Move maneuver, so
terrain costs, occupancy, movement allowance, and declared Wait reactions resolve
through the existing combat turn service.

Withdrawal is accepted only between resolved stages. Pending defenses, unarmed
interactions, Wait interrupts, unsupported-rule blocks, grapples, pins, and
entanglements prevent it. Basic combat requires current authoritative facts that
every remaining participant is unable to see or reach the actor and that further
retreat is feasible. Hex combat requires the actor to have reached a map boundary
while no remaining participant can see or reach them. A visible pursuer therefore
keeps the actor in combat; full chase resolution remains outside this capability.
Square-grid withdrawal remains an explicit adjudication gap.

The command removes the actor from the active initiative and spatial context,
records their complete combatant state, and splits them into a same-scene subgroup
at the existing shared-time frontier. Grounded equipment and battlefield effects
remain with the encounter; actor resources, injuries, and non-spatial effects are
unchanged. One remaining participant completes the encounter. Removing the current
or another participant preserves the current turn, except that the current actor
forgoes that turn and initiative advances normally.

The withdrawn subgroup may queue independent activity immediately. Rejoining uses
the existing representation-specific `join_encounter` placements, requires a
shared-time barrier, and restores the recorded maneuver and defense state. A
returning combatant may rejoin only at the start of an initiative round, preventing
a duplicate turn or defense refresh. Subgroup generations change on both split and
rejoin, so stale queued work cannot migrate between memberships.

Tactical v2 projects a `Leave combat` choice only when a controlled actor currently
passes these checks, including for Basic combat where no hex map is projected. The
command uses the ordinary authorization, campaign CAS, durable receipt, restart,
and replay path. Tactical v1 remains unchanged.
