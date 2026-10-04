# Area targeting for Staff and magic items

This is bounded work under open issue #785, using the supplied Basic Set
Characters third printing B239–240 and Campaigns fourth printing B480–482.
It does not certify the whole named-spell contract or add another spell college.

## Source behavior and real consumers

B482 makes an item's spell use the contained spell's targeting rules. B239
measures Area range to the nearest affected edge and permits sight or touch of
part of that area. The subject is a surface. An actor used to locate its center
does not drag the surface along when moving after casting begins. The paid
radius still sets casting and maintenance energy when only some cells are
selected; the selected portion need not contain the center.

New Area casts on authoritative square and hex maps capture their actual selected
surface and radius at admission. Subsequent unresolved rolls use the caster's
current position, physical blindness or bilateral eye injury, map darkness and
hex line of sight to any affected surface cell. Existing live Light illumination
(B249), including its expiry and reversed effect, is considered at each affected
cell. Standing on an affected surface
provides contact. A hidden actor cannot supply an implicit center merely because
its identifier was guessed; an explicitly authored surface needs no occupant and
does not reveal hidden occupants. Mapless Basic targeting remains unsupported.

Staff intentions may name a personal Area spell with an explicit surface and
radius. The current wielded item's exact physical length reduces range to the
nearest selected part when pointing was declared before casting. A separate
current trusted GM's physical observation can establish Staff contact and remove
both range and unseen penalties. The observation is tied to the fixed surface,
current caster placement, item custody, readiness, body, usable enchantment and
GM authority. Moving the actor originally used as an anchor does not invalidate
contact with the surface; moving the caster does, including a loop returning to
the same position. Personal Staff casting still spends the caster's ordinary
energy. Power on the Staff does not discount those personal spells.

Create Fire's actual standing exposure, recorded hex-path exposure, and observed
square-endpoint exposure now use the same selected cells as targeting. An excluded occupant takes no fire damage merely
because it is inside the enclosing radius. Ending square movement in a selected cell, or traversing one in a recorded hex
path, invokes the
existing B433/B400 hazard procedure and real HP effects. Cancelling the effect
retires its hazards. No second damage or expiry reducer was added.

B236 retargeting translates the recorded center and every selected cell together.
The reviewed new center must actually include the chosen recipient, fit the map,
and change the intended result. An excluded-center shape can use the existing
trusted alternative's explicit position to place it appropriately. Every random
candidate is checked before drawing a target. Historical unmarked casts preserve
their original retarget bytes and exposure behavior.

New-generation Awaken casts on empty surfaces spend the ordinary time, roll and
energy without inventing a subject or leaking whether a hidden actor is present.
Actual occupants still receive the existing B248 waking effects. The private
ledger labels per-subject HT checks; player results and exact retries include only
checks for currently known subjects, while retaining the caster's own checks. GM
history retains every original check and consequence. Current GM seating applies
to both new and old Area-command live retries independently of reducer generation.

Power still applies to an item cast's listed casting and upkeep costs, with its
mana multiplier once. The Area repair preserves paid cost, listed casting time,
selected radius and surface through completion. Current effective skill below
three rejects before distraction/casting dice, payment or committed changes.
GM Area commands require both the current GM seat and deployment trust, including
exact retries. Actor controls, CAS, private event visibility and entropy remain
owned by the shared command pipeline.

## History and compatibility

The private spell command payload records `area_targeting_generation: 1`.
Its targeting snapshot marks only new Area casts. Commands without the marker,
including exact retries and seeded reexecution, retain their prior targeting and
Create Fire exposure behavior. Already-started unmarked casts keep their recorded
execution; a modern completion does not silently reinterpret their commitment.
Existing Regular/Resisted item sight behavior, source-independent cancellation of
unrolled casts, and all previously committed roll consequences remain intact.

No public request, response, authored scenario, event schema or engine version
was changed. Staff's extra radius and surface are private intent data; omitted
fields preserve old serialized intent and observation hashes.

## Executable evidence

- `tests/test_item_area_targeting.py`: hand-entered range 2 rather than center
  distance 4; item Power15 gives target13; radius3 Create Fire with Power1 costs
  5 FP and keeps upkeep2. Blindness changes target13 to8 unless part of the area
  is touched. Partial area excludes real hazards, while actual entry causes 3 HP
  injury for a die6 crossing. Tests inspect timing, costs, paid state, fixed
  geometry, live Light in total darkness, endings, unseen-actor refusal, private
  events, current GM authority,
  stale and changed-intent rejection, exact retries, both stores and seed replay
- `tests/test_staff_area_targeting.py`: approved personal Create Fire skill14,
  a two-yard Staff and nearest edge two yards away produce target14 and actual
  6 FP payment. Trusted contact preserves target14 despite blindness. Caster
  movement invalidates contact without erasing the pointing declaration, giving
  target9. Both stores and full Staff-command seed reexecution are covered
- `tests/test_area_backfire_continuations.py` verifies translated actual hazards,
  real caster HP loss, explicit partial-area placement, candidate admission before
  RNG, map refusal, old-generation behavior and seeded continuation replay
- `tests/test_awaken_area_privacy.py` verifies empty/hidden occupied admission,
  actual waking, current-visibility retry projection, private check retention,
  seeded replay and old-generation current GM seating
- Existing `tests/test_enchanting_source.py`, `tests/test_enchanting_projects.py`,
  `tests/test_enchanting_nighttime.py`, `tests/test_enchanting_settlement.py`,
  `tests/test_magic_item_execution.py`, `tests/test_magic_item_lifecycle.py`,
  `tests/test_power_maintenance_lifecycle.py` and `tests/test_staff_casting*.py`
  remain the construction, project, item-loss, maintenance and Regular evidence

## Open #785 boundaries

The dependencies #745, #746 and #747 were read live on 2026-10-02; all are closed
completed. Their old dependency labels do not block this implementation.

Square movement still lacks a recorded canonical traversal path: the existing
combat engine validates reachability and stores only the destination. Crossing
fire between two safe square endpoints remains unverified under #785. This
repair does not invent a separate path planner.

The supported named-spell baseline and current ownership are reconciled in
[named-spell acceptance](gurps-enchantment-baseline.md). Nearby-nonparticipant
penalties and extra Slow and Sure energy now have actual persisted consumers;
zero-cost Haste wearer behavior and two actual item producers have separate
proofs. A developing held Staff/Deathtouch consumer is not yet credited as merged
or CI-verified here. Overlapping project clocks, unsupported nighttime activities,
director perversion/disaster adjudication, multiple-Power composition and broader
magical sight remain unverified. This Area proof does not certify them.

Power above 100 is not a source cap. The existing `MagicItemBinding` and
`MagicItemInstance` model cap is present in three reviewed authored schema
surfaces: authoring/v1, scenarios/v1 and social/v2. Their generated-schema drift
gates require an intentional contract decision before removing it. This change
neither clamps the real value nor invents an alternative hidden value. The
boundary remains explicitly owned by #785; no snapshot was regenerated.
