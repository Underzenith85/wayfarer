# Basic Combat turn and maneuver contract

Wayfarer's exact Basic Set profile implements the personal-turn and maneuver
contract from *Campaigns*, fourth printing, B362-B368. A combat cycle advances
the shared clock by one second only after every active participant has had an
opportunity to act. Turn order is fixed when combat starts: descending Basic
Speed, then descending DX, with the persisted actor order resolving any final
tie.

The selected maneuver remains on the combatant until that combatant selects a
new maneuver. Its defense restrictions and bonuses therefore survive other
participants' overlapping turns and defense pauses. Aim, Evaluate, Feint, and
Wait commitments are persisted through CAS/replay and consumed by the declared
follow-up exactly once. All-Out Attack (Double) is the one maneuver-owned second
attack supported here; ordinary declarations reject second-attack fields.

## Permission table

`engine.rules.tables.combat.MANEUVER_PERMISSIONS` is the authoritative static
table for all 13 maneuvers. It records movement class, attack count, active
defenses, concentration, and full-turn status. Aim and Concentrate are
full-turn maneuvers. Suppression fire makes its All-Out Attack full-turn through
the option-aware helper. Attack resolution details remain owned by the Basic
Combat resolution work; this table owns the action economy around them.

## Movement

Movement starts from the actor's current Move after inventory encumbrance,
injury, and fatigue. The maneuver table then selects none, Step, half Move, or
full Move, and posture is applied consistently in basic, square, and hex
contexts. Step is one tenth of Move rounded up, minimum one yard; a lying or
sitting combatant cannot translate with a Step. Standing uses full Move,
crouching uses two thirds, kneeling and crawling use one third, lying permits
one yard, and sitting permits none.

Crouching is an explicit free action before or after a maneuver, and rising
from a crouch is free. Crouching after an action is limited to declarations
that permit no more than a Step. Standing-to-kneeling and the reverse may
replace a Step; other posture changes use Change Posture, and rising directly
from lying to standing rejects.

Mapped movement cannot end in an occupied position. Participants with the same
explicit encounter side may be crossed; hostile, neutral, and unclassified
occupants block the path. Evading an enemy and non-human packing densities are
adjacent tactical variants and reject until their owning mechanics provide an
authoritative declaration.

## Evidence

- B362-B363: one-second cycles, fixed Basic Speed/DX order, overlapping personal
  turns, one ordinary maneuver, full-turn exceptions, and maneuver persistence.
- B363-B366: the 13 maneuver declarations, their movement and defense choices,
  Aim/Evaluate/Feint carry-over, All-Out Attack (Double), Concentrate, and Wait.
- B367-B368: Step rounding, posture movement, spacing, occupied paths, and
  crouching.
- `tests/test_gurps_maneuvers.py` supplies the independent table and persistence
  fixtures; `tests/test_combat_timing.py` covers ordering and the shared clock.

No engine version increment is made while the profile remains prerelease.
