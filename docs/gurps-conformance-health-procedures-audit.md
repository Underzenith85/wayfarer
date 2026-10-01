# Hazard, disease, and aging conformance (#837)

The independent expectations in `tests/test_conformance_health_procedures.py`
were entered after inspecting the selected Campaigns fourth-printing PDF,
SHA-256 `79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
The exact assigned source-ledger rows below are frozen at this PR's main base.
These cases exercise existing reducers and resulting HP/FP/permanent-change,
resistance target, immunity, timing and receipt state. No source-row or
certification status is promoted; physiology consumer repairs remain separate.

| Exact source-ledger row ID | Supported source construction and actual consumer | Executable case IDs (`tests/` prefix omitted) |
| --- | --- | --- |
| `section:campaigns:b430:cold` | Winter-clothing complete-ten-degrees penalty; wind-dependent interval; failed resistance costs1FP through `ambient_spec`/`apply_hazard` | `test_conformance_health_procedures.py::test_cold_interval_and_resistance_produce_fatigue` |
| `section:campaigns:b437:poison` | Poison onset and repeated resistance through `apply_toxin` | `test_conformance_health_procedures.py::test_arsenic_onset_resistance_and_first_cycle` |
| `section:campaigns:b437:describing-poisons` | Explicit trusted digestive profile, onset/interval/cycles and HP loss through `apply_toxin` | `test_conformance_health_procedures.py::test_arsenic_onset_resistance_and_first_cycle` |
| `section:campaigns:b438:special-delivery` | Authoritative delivery/protection facts through `apply_toxin` | `test_toxins.py::test_delivery_protection_and_nonpenetration_reject` |
| `section:campaigns:b439:poison-examples` | Arsenic HT-2,1-hour onset/hourly1d,8 cycles; source-specific example | `test_conformance_health_procedures.py::test_arsenic_onset_resistance_and_first_cycle` |
| `section:campaigns:b442:illness` | Persistent authored disease episode in `apply_disease` | `test_conformance_health_procedures.py::test_disease_resistance_precedes_authored_incubation_and_cycle` |
| `section:campaigns:b442:disease` | Resistance, incubation, repeat recovery cycle, acquired immunity in `apply_disease` | `test_conformance_health_procedures.py::test_disease_resistance_precedes_authored_incubation_and_cycle` |
| `section:campaigns:b443:contagion` | Authored close-conversation +2 exposure modifier; later cycles exclude contact modifier | `test_conformance_health_procedures.py::test_disease_resistance_precedes_authored_incubation_and_cycle` |
| `section:campaigns:b444:infection` | Wound age, contamination and antibiotics through `apply_infection_risk` | `test_disease_aging.py::test_wound_infection_uses_recorded_wound_elapsed_time_and_antibiotics`, `test_dirty_wound_failure_creates_the_same_disease_runtime` |
| `section:campaigns:b444:age-and-aging` | Ages50/70/90 cadence, world medical TL-3 to HT, four ordered attribute rolls, ordinary/17/18 losses through `apply_aging` | `test_conformance_health_procedures.py::test_aging_cadence_at_named_source_boundaries`, `test_aging_medical_tl_adjusts_every_attribute_roll`, `test_aging_loss_threshold_is_not_hp_damage` |

The explicit story disease fixture supplies its own delay/cycle/damage as a
supported source construction; it is not presented as a named rulebook disease.
The Arsenic case uses the actual source example. Successful resistance ends that
exposure, and there is no claimed next active cycle for an inactive exposure.
Aging produces pending permanent attribute changes without treating them as HP
injury; the existing approval/build mutation boundary remains intact. No Luck
eligibility, physiology exception, lifespan trait, or medical treatment authority
is inferred from these ordinary constructions.

Existing independently entered source cases continue to own acid, atmosphere,
electricity, ignition/object damage, pressure/acceleration, radiation, seasickness,
vacuum, drug withdrawal and overdose. Their exact consumers and boundaries remain
in `docs/gurps-hazards.md`, `gurps-toxins.md`, `gurps-disease-aging.md` and the
associated test modules. This PR adds onset/resistance/interval/recovery and
numeric edge evidence; it is not a claim of every disease, poison or inherited
trait combination or of end-to-end player compliance.
