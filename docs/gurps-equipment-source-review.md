# GURPS equipment field source review

Reviewer: OpenAI Codex, 2026-09-14.

This review compares the typed equipment ledger with the supplied selected
printings. It covers every field declared by `AUDITED_MODELS` and every special
behavior exported by `equipment_audit.rows()`. It does not promote unsupported
mechanics or omitted catalog rows.

## Source identity

- Basic Set: Characters, Fourth Edition, third printing (February 2008),
  SHA-256 `872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e`.
- Basic Set: Campaigns, Fourth Edition, fourth printing (April 2008),
  SHA-256 `79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.

Printed Characters pages map to PDF page `B+2`; printed Campaigns pages map to
PDF page `B-335`. Printed footers were checked before comparing the records.

## Comparison performed

- Characters B264-B289 was reviewed page by page for the equipment table
  vocabulary: TL, damage, Acc, range, weight, RoF, shots, cost, minimum ST,
  bulk, recoil, location, DR, flexibility, defense bonus, reach, parry, weapon
  quality, ammunition and container capacity.
- The B270 double-dagger marker supplies the exact 1.5x minimum-ST threshold
  for post-attack readiness. The B272 monowire-whip expression supplies a
  separate d6 term added to ST-based swing damage; it is not a fixed-damage row.
- Characters B272-B274 and Campaigns B397/B405-B406 were checked for starred
  reach changes, lodged picks, couched-lance damage, cutlass-hilt strikes,
  purchased whip lengths, and chainsaw bonus-die/readiness behavior.
- Characters B287 notes 2-5 were checked for shield bashes and spikes, buckler
  skill/rush handling, iron and plastic-riot construction, and the force
  shield's superscience marker, LC3, and hardened DR 100 with no finite HP.
- Campaigns B368/B371/B406 shield rushes dispatch through the Slam collision
  procedure: relative velocity and both HP totals determine damage, shield DB
  augments the rusher's roll, and the shield receives reciprocal damage.
- Characters B16, B178, B181, B195, B198, B205, B211, B222 and B270 were
  checked for rated ST, mounted/crew-served weapons, entangling attacks,
  readiness, sprayers and launcher-assisted throws.
- Campaigns B376, B378, B381-B383, B399, B407-B415, B471-B472, B483-B485 and
  B556-B557 were checked for parrying projectiles, penetration, firearm
  failures, tight-beam attacks, rapid fire, multiple projectiles, grenades,
  sights, explosions, electronics, object durability and breakage.
- Adapter discriminators (`MeleeMode.kind` and `RangedMode.kind`) were confirmed
  to have no source column. Their explicit gaps are reviewed source findings;
  they remain mechanically omitted.

## Corrections found

The comparison corrected three ledger claims:

- `RangedMode.recoil` is a recoil score whose interval controls additional
  rapid-fire hits, not a divisor.
- `FirearmSpec.fuse_seconds` is anchored to the hand-grenade delay on B410,
  rather than the malfunction and explosion pages.
- `EquipmentProfile.power_cell_capacity` is anchored to the beam-weapon Shots
  and power-cell records on B280, rather than the explosion pages.

All other declared units and anchors agree with the selected source. Executable
tests remain the implementation evidence; this document is the independent
source-comparison evidence for the field review state.

Issue #511 adds an audited `RangedMode.guidance` adapter and nested guidance
family, seeker-skill, and seeker-sense fields from Campaigns B412-B413. A
special weapon is available only when its equipment row supplies the complete
adapter; technology level and weapon names never imply guidance support. Cover
remains an object-durability concern documented in
`gurps-special-ranged-procedures.md`.

## Ranged and heavy-equipment completion (#685, #702, #703)

Characters B275-B281 was rechecked row by row for the combined ranged-equipment
completion. Every muscle-powered, firearm, beam, and heavy-weapon row in scope
now has a typed catalog profile. B281's 13 weapon rows are paired with separate
ammunition or fuel records, including missile and rocket explosive warheads.

The review also checked the applicable notes for bodkin and lead missiles,
alternative firearm ammunition, poison or drug follow-ups, electrolaser linked
afflictions, surge, Bulk-based Holdout, rated crossbows, minimum range,
guidance, crew and mounting, integral launchers, back blast, and liquid-projector
streams. Their ledger dispositions point to executable tests in
`tests/test_issue_685_ranged_heavy.py`; unrelated melee linked-affliction work
remains separately fail-closed.
