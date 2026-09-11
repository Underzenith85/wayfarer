# Combat timing

Wayfarer keeps two related timelines without pretending they are the same thing.
Campaigns B362 defines each combatant's turn as a one-second interval that overlaps
the other combatants' turns. A complete initiative cycle therefore contributes one
shared second, not one second per command or participant.

## Actor-relative boundaries

The encounter advances to the next actor after the acting maneuver and all pending
defense or unarmed choices resolve. At that actor's turn boundary, the engine resets
that actor's repeated-parry and Block use, retreat availability, transient defense
penalties, and one-turn maneuver state. It does not reset the same state for every
participant at a round boundary. Injury start/end phases use the same actor-relative
boundary. A Wait reaction runs inside the interrupted actor's turn, and resume or
cancel completes only that interrupted turn.

## Shared clock

Each encounter contributes completed initiative cycles to its subgroup's
`ready_through` frontier. The campaign clock advances only to the minimum frontier
across subgroups, so two simultaneous fights overlap and a fight does not add its
seconds to an investigation already occurring elsewhere. Scheduled resources and
world effects settle at each shared timestamp before queued activities, using stable
IDs for deterministic ordering.

If incapacitation ends an encounter after a turn completes partway through a cycle,
the final partial cycle contributes one shared second. This prevents the decisive
turn from becoming free. An explicit GM `EndEncounter` at an unchanged turn cursor
is a zero-time lifecycle command.

Pending defenses, unarmed choices, unresolved Wait interruptions, and blocked
adjudications never advance the shared frontier. Durable command receipts and the
campaign compare-and-set boundary ensure retry, reconnect, and replay cannot charge
the same second twice.

## Evidence

- `tests/test_combat_timing.py` covers cycle arithmetic, actor-relative defense
  resets, concurrent fights, the minimum-frontier barrier, retry, restart, and replay.
- `tests/test_unarmed_wait.py` covers reaction/resume ordering and proves the Wait
  reaction does not manufacture another actor turn.
- `tests/test_wave9.py::test_combat_barrier_long_investigation_and_reinforcement_arrival`
  covers combat overlapping a longer queued activity.
- `tests/test_ability_service.py::test_ability_concentration_obeys_combat_turn_and_shared_round_clock`
  covers concentration at the actor and shared-clock boundaries.

Rules reference: GURPS Basic Set, Campaigns, fourth edition, B362-B366 and B374.

