# GURPS physiology traits

The optional `package:gurps-basic-physiology-traits@1.0.0` package pins all 37
physiology entries in issue #235. Trusted parameters price radiation divisor,
regeneration rate, Dependency, Weakness, Bestial, Cold-Blooded, Sleepy,
Stress Atavism, and Unhealing variants.

Approved builds project breathing, sustenance, sleep, lifespan, radiation,
regeneration, pressure, vacuum, contaminants, and temperature behavior.
Authored due intervals apply regeneration, Dependency or Weakness damage, and
limited Extra Life revival directly to the existing HP pool. Writes require
actor authority and the expected resource revision; identical command retries
return the recorded outcome and serialized histories restart unchanged.

The selected-printing review and #235 implementation evidence are reconciled;
these inventory rows are verified.

## Regeneration settlement (#748)

B80 rates derive HP from the approved purchase: Slow restores 1 HP per 12 hours
(in addition to normal healing), Regular 1 HP per hour,
Fast 1 per minute, Very Fast 1 per second, and Extreme 10 per second. B424 scales
these amounts for each full 10 maximum HP (minimum multiplier 1). Authored
`amount` does not override these rates. Radiation recovery remains
outside the approved variants in this package; Radiation Only cannot restore HP.

Each actor's due tick is consumed once even with another interval or command ID.
The first tick must follow a full purchased period from `started` (zero by
default); later ticks must follow the last consumed tick by at least that period.
Intervals are aligned to their start. Canonical healing preserves injury facts,
illness restrictions, death status, and maximum HP. Exact retries survive
serialization, and altered request payloads conflict.

`tests/test_physiology_traits.py` exercises B80/B424 rates, duplicate ticks,
restart/retry behavior, early and misaligned ticks, HP caps, and death rejection.
This scoped evidence does not certify unsupported Regeneration variants.
