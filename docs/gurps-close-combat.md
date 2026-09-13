# Close and multi-hex combat

Issue #508 completes the engine-owned procedures selected from *Basic Set:
Campaigns*, fourth printing (April 2008), B391-B392. The implementation is in
`engine.simulation.combat.close_combat`, the existing tactical movement and
attack modules, and the context-owned hex placement records.

Close combat is an explicit encounter relationship. A mapped combatant enters
an occupied enemy hex only when the command selects close-combat entry and a
legal target; the engine validates the whole movement before recording every
relationship in that shared hex. A controlled combatant cannot leave. An
uncontrolled combatant may use the supported exit through its own side; crossing
the opponent's side rejects until an explicit evasion procedure is supplied.
Readying in the shared hex uses the existing recorded-DX-check path.

Melee attacks and armed parries in the shared hex require Reach C. Dodge remains
available and Block does not. Ranged attacks use the selected mode's Bulk in
place of speed/range. Attacks from outside a shared hex persist a bystander order
chosen from recorded entropy before the attack resolves; narration never selects
a more convenient target afterward. Cooperative takedown and pin helpers use a
typed bounded formula.

A `HexActorPlacement` may own an absolute `occupied_hexes` footprint. The head
remains the movement and facing reference. Movement rotates and translates the
entire footprint, validates every destination cell and collision, and only then
updates the encounter, so a failed body placement cannot partially move the
entity. One-hex placement serialization remains unchanged.

The independently derived acceptance fixtures live in
`tests/test_close_combat.py`. They cover explicit entry and exit, legal defenses,
entropy-owned bystander order, helper limits, footprint integrity, rotation and
atomic collision rejection.
