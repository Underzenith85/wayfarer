# Surprise, visibility and personal high-speed movement (#509)

The Basic Set engine implements this source slice against *Campaigns*, fourth
printing (April 2008), B393-B394. Expectations in
`tests/test_special_combat_situations.py` were independently derived from the
selected printing; no rulebook prose is stored in the repository.

## Surprise and initiative

Surprise is a GM-authorized, pre-first-turn transaction. The resolver supplies
two complete, disjoint sides and their leaders. The engine derives leader IQ,
Tactics and Combat Reflexes modifiers from approved builds, consumes recorded
dice only when the selected surprise kind calls for an initiative contest, and
persists both side results on the encounter. Total-surprise freeze duration and
per-actor recovery remain in the injury aggregate, where turn-start IQ checks
already enforce Do Nothing until recovery (B393).

The persisted trigger, sides, leaders, rolls, modifiers, scores, winning side
and freeze duration make restart and retry outcomes inspectable. Separate
encounters retain separate initiative and surprise state; resolving one group
does not reorder another encounter.

## Visibility

Basic combat visibility facts are directed. A plain false fact continues to mean
an impassable line-of-sight boundary. A GM may instead identify invisibility,
smoke or darkness and record whether the observer has located the target and is
aware of the incoming attack. Detection itself uses the existing sensory-check
service; an unknown location cannot be converted into an entity-targeted attack.

Once location is authoritative, the combat preparation transaction derives the
B394 attack penalty, limits an aware defender to Dodge unless the attacker was
also located, applies the defense penalty, and records those values in the
pending-defense receipt before any attack roll. An unaware defender receives no
active defense. Hidden actors remain absent from player projections even when a
private location fact permits a blind attack; guessed identifiers collapse to
the same unavailable-target error.

## Personal high-speed movement

Personal high-speed movement is deliberately separate from vehicle motion. A
typed turn flag enters the state only after a standing combatant supplies a full
Move or Move-and-Attack path with no more than one gradual direction change. The
next-turn velocity, current direction and straight-line distance are saved on
the combatant (B394).

While active, the actor must use Move or Move and Attack and supply a path whose
length equals the saved velocity. Every step, facing change, turning-radius
constraint, map cell and occupied hex is validated before the immutable
combatant replacement is returned. Difficult-terrain slowing, acceleration,
braking, skids and collisions begin on B395 or belong to vehicle operation; this
B394 transition rejects those cases rather than borrowing the vehicle reducer.
Hex-to-Basic conversion likewise rejects active high-speed state because a
mapless context cannot preserve its direction and turning budget.

Evidence: `tests/test_special_combat_situations.py`,
`tests/test_physical_traits.py`, `tests/test_basic_combat.py`, and
`tests/test_tactical_completion.py`.
