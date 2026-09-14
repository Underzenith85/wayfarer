# Equipment, injury, and recovery capability certification (#728)

Issue #728 reconciles thirteen Basic Set capability rollups against the selected
*Characters* third printing and *Campaigns* fourth printing. The machine-readable
matrix is
`tests/fixtures/gurps/equipment-injury-recovery-certification.json`; its expected
outcomes were checked from those source artifacts rather than generated from the
runtime.

| Capability group | Required variants | Runtime evidence |
| --- | --- | --- |
| Weapon profiles | melee modes; ranged modes and ammunition; load and readiness | exact table fields, legal modes, resource conservation and retry |
| Armor profiles | location DR; shields; split/front/layered/concealed armor | numerical DR selection, shield condition and persistent penalties |
| Equipment catalog | row/field reconciliation; special behavior; package and conservation | all Basic Set equipment audit scopes and fail-closed selection |
| Object durability | construction HP; penetration and breakage; repair | integer HP bounds, HT/threshold transitions, custody, parts and replay |
| Damage types | ordinary modifiers; structural tolerances; area/special damage | integer injury, direct/area classification and authoritative HP state |
| Damage resistance | penetration; location/natural DR; special armor | effective DR and resulting injury at the shared reducer |
| Armor divisors | positive; fractional; tight-beam/chinks | required rounding, bare-skin minimum and targeting integration |
| Hit locations | targeted; random; wounding/crippling | penalties, anatomy, tables, caps, major wounds and durable rolls |
| HP thresholds | shock/stun; consciousness/movement; death | exact boundary transitions and once-only threshold settlement |
| Lasting wounds | duration class; functional effects; critical aftermath | durable impairment, expiry/permanence and replay |
| Fatigue | signed FP; low-FP effects; rest restrictions | HP spillover, rounded derived values and per-cause recovery |
| Healing | natural; high-HP/care scaling; healing drugs | elapsed-time settlement, capped healing and one-dose conservation |
| Medical treatment | first aid; physician care; surgery/trauma | attempt budgets, concurrency, interruption, materials and replay |

Each matrix variant names reviewed source-ledger rows, a nonempty independent
expected result, real engine modules, and tests that assert numerical or durable
state outcomes. The catalog row additionally joins all Basic Set equipment
catalog, section, footnote, field-provenance and package-binding inventory scopes.
The integrity test fails if a source row loses review/readiness, a runtime or test
binding disappears, an owned inventory count drifts, or a promoted capability
loses #728 ownership.

The reviewed optional-rule and Infinite Worlds boundaries are unchanged. This
promotes only the engine-only Basic Set evidence. It does not claim the unavailable
Lite source artifact, enable a player-facing profile, add API/UI behavior, or
change prerelease engine versioning.
