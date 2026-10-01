# Injury numerical conformance (#836)

The source inspection selected Campaigns fourth printing, SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
This matrix freezes the exact source-ledger row IDs below. Expected numbers in
`tests/test_conformance_injury.py` are independently transcribed from B378-381,
B419-423. It executes the existing authoritative injury reducer and inspects
resulting HP, injury status, HT-check targets, lasting-injury durations, receipts,
and eligibility. No capability/source-row/gate status is promoted.

| Assigned source row ID | Supported branch and actual consumer | Executable case ID (`tests/` prefix omitted) |
| --- | --- | --- |
| `section:campaigns:b378:damage-resistance-and-penetration` | Integer armor division, sub-one divisor, penetrating damage in `apply_injury` | `test_conformance_injury.py::test_armor_division_precedes_penetration` |
| `section:campaigns:b379:wounding-modifiers-and-injury` | All ten ordinary torso damage types, floor after multiplier and minimum penetrating injury | `test_conformance_injury.py::test_torso_penetration_and_every_damage_multiplier` |
| `section:campaigns:b380:effects-of-injury` | Canonical HP/status in `apply_injury`, reeling movement in `impaired_movement` | `test_conformance_injury.py::test_major_wound_requires_more_than_half_maximum_hp`, `test_reeling_uses_strict_one_third_and_rounds_up` |
| `section:campaigns:b418:injuries` | Explicit Basic HP pool and server-authority injury settlement | `test_conformance_injury.py::test_multiple_death_thresholds_and_resource_receipt_are_exact` |
| `section:campaigns:b419:general-injury-lost-hit-points` | Odd HP11 death thresholds, strict reeling threshold, automatic death at -5 maximum HP | `test_conformance_injury.py::test_multiple_death_thresholds_and_resource_receipt_are_exact`, `test_reeling_uses_strict_one_third_and_rounds_up`, `test_minus_five_maximum_hp_is_automatic_death` |
| `section:campaigns:b419:shock` | Maximum HP complete-tens divisor and fractional injury boundary in `apply_injury` | `test_conformance_injury.py::test_high_hp_shock_divisor_and_fraction_boundary` |
| `section:campaigns:b420:major-wounds` | Strict greater-than-half maximum HP with odd HP11 | `test_conformance_injury.py::test_major_wound_requires_more_than_half_maximum_hp` |
| `section:campaigns:b420:knockdown-and-stunning` | Failure by1 versus failure by5; resulting prone/stunned/unconscious state | `test_conformance_injury.py::test_knockdown_failure_and_failure_by_five`; stun recovery remains covered by `test_injury.py::test_stun_recovery_is_end_of_forced_turn_and_retry_safe` |
| `section:campaigns:b420:crippling-injury` | Existing living-human limb caps, severing and lasting injuries through `apply_injury`/`ResolveCrippling` | `test_hit_locations.py::test_golden_wounding_and_armor`, `test_severing_and_funny_bone_thresholds`; new `test_conformance_injury.py::test_source_lasting_injury_duration_and_medical_tl` |
| `section:campaigns:b421:patient-status` | HP bands in `patient_status` | `test_injury.py::test_patient_status_boundaries` |
| `section:campaigns:b421:temporary-attribute-penalties` | Shock expires on following turn; persistent stun recovery through `InjuryTurn` | `test_injury.py::test_stun_recovery_is_end_of_forced_turn_and_retry_safe` |
| `section:campaigns:b423:mortal-wounds` | Death failure by1/2 mortal, by3 dead; half-hour due time | `test_conformance_injury.py::test_death_failure_margin_mortal_boundary` |
| `section:campaigns:b423:death` | Automatic death at exact -5 maximum HP without RNG | `test_conformance_injury.py::test_minus_five_maximum_hp_is_automatic_death` |

The new independent matrix confirms these existing reducer branches; it did not
reveal a repair for the assigned branches. The pre-existing injury tests' historic
knowledge-based header is not used as source verification for the new cases.
The selected-source check here records the new numerical expectations explicitly.

Other source branches are owned by their existing bounded workstreams: attack
rolls/critical results and knockback by combat conformance, special damage by
registered special-damage consumers, inherited Injury Tolerance forms and
nonhuman anatomy by physiology/creature audits, and recovery by canonical medical
procedures. Optional injury rules retain their disabled profile decisions.
Instant death by GM-authored obviously lethal context and dying-action guidance
are not inferred from an ordinary HP wound. No test in this matrix certifies those
operations, optional variants, all trait combinations, or an end-to-end player
workflow. Unsupported construction remains visible in its owning audit issue.
