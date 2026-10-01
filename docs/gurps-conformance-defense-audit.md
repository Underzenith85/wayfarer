# Defense and location numerical conformance (#835)

This PR freezes the ten source-ledger row IDs below from
`src/wayfarer/certification/basic_set_audit/sections.json` at its main base.
All refer to the selected Campaigns fourth printing, SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
Expected numbers in the new executable tests were independently entered after
inspection of that artifact. No capability or source-ledger status is promoted.

| Frozen source row ID | Supported construction and actual consumer | Executable case ID or bounded missing-operation owner |
| --- | --- | --- |
| `section:campaigns:b374:defending` | Ready equipment and posture compose in `defense_value`; explicit defender state | `test_conformance_defense.py::test_posture_and_equipment_defense_numbers` |
| `section:campaigns:b374:active-defense-rolls` | Ordinary defense eligibility and resulting active-defense score; authority, retry and stale revision through `CombatService.execute` | `test_conformance_defense.py::test_basic_fencing_retreat_transition_retry_and_authority` |
| `section:campaigns:b374:dodging` | Basic Speed rounding, encumbrance and ready shield DB in `score_defense`; ordinary Dodge | `test_conformance_defense.py::test_posture_and_equipment_defense_numbers`; Acrobatic and Sacrificial Dodge remain unverified, #878 |
| `section:campaigns:b375:blocking` | Shield skill half rounded down +3, DB, and per-turn eligibility in `score_defense` | `test_conformance_defense.py::test_posture_and_equipment_defense_numbers`, `test_gurps_melee.py::test_dodge_parry_block_and_repeats` |
| `section:campaigns:b376:parrying` | Ordinary/fencing mode, repeated parry and ready shield DB in `score_defense` | `test_conformance_defense.py::test_fencing_retreat_adds_three_against_same_foe`, `test_gurps_melee.py::test_unbalanced_and_fencing_parry_columns` |
| `section:campaigns:b376:parrying-heavy-weapons` | Explicit breakage metadata, BL limit and weight threshold in `score_defense` | `test_gurps_melee.py::test_heavy_weapon_requires_explicit_breakage_metadata` |
| `section:campaigns:b377:active-defense-options` | Ordinary/fencing retreat through `prepare_defense` and `score_defense`, persistent distance and foe-bound bonus | `test_conformance_defense.py::test_basic_fencing_retreat_transition_retry_and_authority`; Dodge and Drop remains unverified, #878 |
| `section:campaigns:b398:hit-location` | Named living-human targeted penalties and shield-side doubling in `attack_penalty` | `test_conformance_defense.py::test_hit_location_independent_penalties`, `test_conformance_defense.py::test_shield_side_doubles_only_protected_limb_penalty` |
| `section:campaigns:b400:targeting-chinks-in-armor` | Source-approved damage kind and worn armor, attack penalty and halved DR in melee resolution | `test_special_melee_procedures.py::test_b400_chink_penalty_is_derived_from_location_and_damage_kind`, `test_special_melee_procedures.py::test_b400_chink_attack_uses_worn_armor_and_halves_dr` |
| `section:campaigns:b400:striking-at-weapons` | Weapon-target penalty and authoritative object injury in melee resolution | `test_special_melee_procedures.py::test_b400_weapon_hit_dispatches_through_object_damage`, `test_projectile_objects.py::test_projectile_weapon_target_has_no_block_or_shield_bonus` |

Test filenames above are under `tests/`. New source-derived cases cover the
runtime's standing/kneeling/prone representation. Other postures, optional rules,
and inherited trait modifiers are not inferred from these cases. Adjacent source
rows Damage and Injury (B377) and Tight-Beam Burning Attacks (B399) belong to the
injury matrix #836; Flying Combat (B398) belongs to its existing movement/flight
consumer evidence. They are not claimed by this defense matrix.

The independent fencing case exposed an actual source discrepancy: the existing
retreat transition correctly added ordinary Parry's +1 but the selected fencing
mode did not supply its additional +2. The repair adds that amount only when the
pending attack's actual attacker matches the defender's stored retreat foe.
Another foe receives no fencing retreat bonus. Ordinary parry remains +1.

The options recorded in #878 have no authoritative selected active-defense
command and settlement consumer. The inspection included `ChooseDefense`,
`prepare_defense`, `score_defense`, and melee/ranged resolution. Their verification
is deliberately still open; a generic Acrobatics task or a named source section
is not executable evidence. Focused engine tests do not close end-to-end gates.
