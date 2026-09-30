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

The #235 inventory rows retain their recorded selected-printing review. Runtime
support remains limited to the explicitly described variants below.

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

## Extra Life settlement (#749)

The unmodified Extra Life interval requires a canonical Basic Set HP pool with
an actual `injury.dead` fact. Negative HP alone is not death. A successful
revival retains the existing full-HP settlement policy and clears death,
unconsciousness, mortal-wound deadlines, shock and stun. Actor anatomy,
physical traits, durable injuries and position remain intact. Revival records
consume one purchased life; exact request retries return their original receipt,
and a successful interval cannot spend another life under a new command ID.
New requests still require system authority and the expected resource revision.

Copy, Reincarnation and Requires Body revival variants are unsupported and
reject generic settlement. This correction addresses audit A08's contradictory
living/dead state; it does not certify GM return timing, alternate bodies or
other Extra Life variants. Direct rechecking of Characters third-printing B55
is still required before promoting this scoped evidence to source certification.

`tests/test_physiology_traits.py` inspects the resulting injury state and
serialized eligibility, living negative HP, actual death at positive HP,
remaining-life exhaustion, duplicate intervals, exact and changed retries,
stale revisions and authority rejection.
