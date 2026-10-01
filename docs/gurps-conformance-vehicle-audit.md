# Vehicle numerical conformance and catalog join (#838)

Source inspection used the selected Campaigns fourth printing, SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
The exact assigned source rows below are frozen for this PR. New tests contain
independently entered numerical expectations and inspect real transport motion,
object HP, revision and receipts. No capability or source-ledger status changes.

| Assigned row ID | Supported construction and actual consumer | Executable case IDs (`tests/` omitted) |
| --- | --- | --- |
| `section:campaigns:b462:vehicles` | Explicit initialized owned object body and known occupants; two registered sample entries bound by `bind_vehicle` | `test_conformance_vehicles.py::test_registered_vehicle_source_stats_reach_runtime_movement`, `test_catalog_join_rejects_wrong_body_and_unknown_entries` |
| `section:campaigns:b462:vehicle-statistics` | HP/DR, Handling/Stability and acceleration/top-speed split reach runtime | `test_conformance_vehicles.py::test_registered_vehicle_source_stats_reach_runtime_movement` |
| `section:campaigns:b463:basic-vehicle-movement` | Acceleration moves real transport pose through `apply_transport`, preserves exact reload retry and stale rejection | `test_conformance_vehicles.py::test_registered_vehicle_source_stats_reach_runtime_movement` |
| `section:campaigns:b464:ground-vehicle-table` | Catalog IDs `vehicle:wagon` and `vehicle:luxury-car`, selected B464 values | `test_conformance_vehicles.py::test_registered_vehicle_source_stats_reach_runtime_movement` |
| `section:campaigns:b467:basic-vehicle-combat` | Source body DR/HP reaches `DamageVehicle` and object injury | `test_conformance_vehicles.py::test_registered_vehicle_source_stats_reach_runtime_movement`; existing `test_vehicle_modes.py::test_declared_ram_has_separate_attack_and_dodge_before_collision_exchange` |
| `section:campaigns:b430:collisions-and-falls` | Collision dice HP×relative speed/100; exact sub-die and nearest-whole boundaries, hard-obstacle doubling | `test_conformance_vehicles.py::test_collision_dice_source_rounding_edges` |
| `section:campaigns:b431:hit-location-from-a-fall` | Existing armor/restraint injury consumer, without generic hit-location inference | `test_vehicle_modes.py::test_b431_b432_armor_and_restraints` |

The source sample catalog previously carried only listing HP/DR/price and exposed
no runtime join. `bind_vehicle` now projects both samples to mechanics version2,
checking their registered IDs, required capabilities, exact initialized unliving
body HP/DR, ownership, uniqueness and source occupant capacity. It creates no
objects, heals no injury, writes no state and fabricates no occupants. The caller
persists the explicit result through its ordinary setup/CAS boundary.

Source numeric inputs: Wagon HP35/DR2/Hnd-3/SR4/Move4/8/one occupant; Luxury Car
HP57/DR5/Hnd0/SR4/Move3/57/five occupants. The actual movement consumer receives
these values and the actual damage consumer loses8 or5 body HP from10 crushing
damage. Catalog IDs remain separate from wearable/carryable inventory IDs.
Calling the unbound listing's `require_operation` still rejects: an initialized
owned body and explicit occupant binding are necessary.

This two-entry join does not expand the B464-465 complete vehicle tables. Wagon
draft animals, fuel/range, mass/load, road-bound overland travel, cargo, cabin,
weapon mounts and other terrain/locomotion facts retain explicit authored
scenario/consumer boundaries; the adapter does not infer them from the sample
name. Existing per-mode source tests continue to own control-loss, water/air/space
movement, collisions, occupant aftermath and restart. Exact vehicle sample
motion/object-damage tests are evidence for the join, not universal vehicle or
end-to-end compliance. The broader catalog source rows remain listing-only in
release accounting until their complete constructions are independently audited.
