# Basic Set combat capability certification (#727)

This is the human-readable index for
`tests/fixtures/gurps/combat-capability-certification.json`. The fixture is the
authoritative variant-level matrix: every variant names its independent expected
outcome, selected-source rows, owner inventory rows, runtime modules, and behavior
tests. The source baseline is *Characters*, third printing (February 2008), plus
*Campaigns*, fourth printing (April 2008).

| Capability | Required variant groups | Executable evidence |
| --- | --- | --- |
| `gurps.combat.active_defense` | Dodge/Block/Parry; retreat and defense options; maneuver restrictions and heavy-weapon durability | `test_gurps_melee.py`, `test_defense_scoring.py`, `test_issue_684_melee_shields.py`, `test_melee_parry_modes.py` |
| `gurps.combat.grappling` | Grip/control/escape; takedown/pin/arm lock; choke/release/close movement | `test_unarmed.py`, `test_unarmed_parry_lock.py`, `test_choke_hold.py`, `test_close_combat.py` |
| `gurps.combat.maneuvers` | Complete permission table; All-Out and Move and Attack options; Wait/Ready/posture movement | `test_gurps_maneuvers.py`, `test_maneuver_followups.py` |
| `gurps.combat.melee_attack` | Declaration/skill/reach/hit; defense/damage/injury; critical and object effects | `test_gurps_melee.py`, `test_special_melee_procedures.py`, `test_melee_parry_modes.py`, `test_critical_continuation.py` |
| `gurps.combat.melee_weapon_skills` | Weapon-class dispatch; fencing/shield/unarmed contracts; procedure replay | `test_melee_skill_procedures.py`, `test_issue_684_melee_shields.py` |
| `gurps.combat.ranged_weapon_skills` | Launcher/thrown/entangling rows; TL/mount/stream/innate rows; defaults and runtime dispatch | `test_ranged_skills.py`, `test_entangling_attacks.py`, `test_tl_indexed_ranged_skills.py`, `test_mounted_ranged_skills.py`, `test_innate_attack_specialties.py`, `test_ranged_default_gaps.py` |
| `gurps.combat.technique_procedures` | Parent-specific ranges; offensive/defensive/mounted effects; optional-rule boundary | `test_combat_technique_procedures.py`, `test_unarmed_parry_lock.py` |
| `gurps.combat.turn_timing` | Initiative/one-second turns; Wait pause/resume; surprise and partial-cycle settlement | `test_combat_timing.py`, `test_maneuver_followups.py`, `test_unarmed_wait.py`, `test_special_combat_situations.py` |
| `gurps.combat.unarmed` | Punch/kick/training; defense and critical effects; maneuvers/Wait/close combat | `test_unarmed.py`, `test_unarmed_critical_followups.py`, `test_unarmed_double_defense.py`, `test_unarmed_integrations.py`, `test_unarmed_wait.py`, `test_close_combat.py` |
| `gurps.tactical.facing` | Front/side/rear arcs; facing-change costs; multi-hex facing | `test_hex_geometry.py`, `test_special_combat_situations.py`, `test_close_combat.py` |
| `gurps.tactical.hex_movement` | Hex path and movement cost; reach/LOS/close combat; high-speed movement | `test_hex_geometry.py`, `test_close_combat.py`, `test_gurps_melee.py`, `test_special_combat_situations.py` |
| `gurps.tactical.visibility` | Attacker visibility; unseen-attacker defense; light/smoke/surprise | `test_special_combat_situations.py` |

`tests/test_combat_capability_certification.py` rejects missing families,
variants, expected outcomes, unreviewed or incomplete source rows, absent engine
or test paths, and unresolved owner-inventory gaps. The referenced behavior suites
exercise numerical results and persisted state through the existing command,
CAS, recorded-entropy, replay, injury, and resource boundaries.

These twelve capabilities are verified for the Basic Set target. This evidence
does not make a separate GURPS Lite source claim, does not enable Infinite Worlds,
and does not change any runtime profile or engine version. Engine versioning stays
unchanged while the engine remains prerelease.
