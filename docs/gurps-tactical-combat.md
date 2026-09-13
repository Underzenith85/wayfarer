# Tactical combat completion (#507)

The Basic Set profile routes mapped movement and attacks through the existing
combat transaction. The implementation is source-reviewed against *Campaigns*,
fourth printing (April 2008), B384-B391; tests derive their expectations
independently and do not reproduce rulebook text.

## Executable boundary

- A supplied hex path is validated in full before commit. Direction, facing,
  posture, authored terrain, stairs, allied obstructions, hostile occupancy and
  the maneuver's movement allowance contribute to the same movement receipt
  (B384-B387). A failed path consumes neither movement nor a turn.
- Step ignores movement-point surcharges, permits its full distance in any
  direction and may finish at any facing. Move, Move and Attack, All-Out Attack
  and Increased Dodge retain their distinct facing and allowance policies
  (B385-B387).
- Attack reach and the deferred post-attack Step share the selected weapon mode.
  This supports long-weapon spacing without a second movement action (B388).
- A pop-up attack is one typed Attack transaction. It validates one adjacent
  exposure and an immediate return, requires the original pose to be out of
  sight, resolves range and line of fire from the exposure pose, discards prior
  Aim and applies the tactical attack penalty. Bows, slings, Basic combat and
  mixed movement options reject before dice (B390).
- The pending-defense receipt records the attack approach. Side attacks carry
  the tactical defense adjustment, true rear attacks offer no active defense,
  and a path that began in front but ended behind is retained as a runaround
  rather than being misclassified after restart (B390-B391).
- A hex-zone Wait stops movement at the first matching path entry. The paused
  command records only the remaining path, so resume neither repeats movement
  nor skips the unused portion (B385).

Basic-to-hex and hex-to-Basic conversion continue to copy the complete
combatants and therefore preserve maneuver commitments, ready equipment, Aim,
Evaluate and Wait state. Conversion still rejects consequential terrain and a
pending interaction rather than silently discarding tactical facts.

## Explicit adjacent boundaries

Entering, leaving and fighting in an enemy's hex, evasion, multi-hex bodies,
close-combat readying and attacks through occupied firing lines belong to #508
or later special-ranged work. This slice keeps hostile transit fail-closed and
does not infer evasion, cover, traits, handedness or multi-hex geometry from a
client path. Opportunity-fire area selection remains separately owned; ordinary
Wait zones consume the authoritative path entry supplied here.

Evidence: `tests/test_tactical_completion.py`, `tests/test_hex_geometry.py`,
`tests/test_tactical.py`, `tests/test_maneuver_followups.py`, and
`tests/test_basic_conversion.py`.
