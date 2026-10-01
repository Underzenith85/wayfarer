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

## Persisted harmful physiology host (#750)

Rechecked Characters, third printing, B130 and B161. The internal
`HarmfulPhysiologyService` now owns recorded exposure, dose/contact satisfaction,
and campaign-calendar commands through `CommandPlan`. It uses the current
approved actor under the campaign lock, canonical HP/injury, and command-seeded
randomness. The private resource-event ledger persists each actor/condition's
purchase fingerprint, observation, accrued contact, and next deadline; it is
rebuilt from the existing event stream without another state schema.

An observation is an explicit trusted-director fact at the current game clock.
For Weakness it records whether the approved harmful condition is present. For
Dependency in dose mode, presence records an actually administered dose (or an
ongoing supply for Constantly); a first absence records an already-missed dose
as of the current clock. It does not pretend to reconstruct unrecorded exposure
history. Object/environment Dependencies use contact mode. Partial contact
accumulates until the source duration is met; an observation alone cannot reset
the missing-dose clock. B130 does not impose uninterrupted contact, so the host
does not discard elapsed contact when the actor leaves briefly. Continued
satisfying contact stays safe, and leaving starts the next required interval.

The purchased Weakness rates are one die per 60, 300, or 1,800 seconds. Dependency
loses one HP per 60 seconds while its constant supply is missing; after missing
an hourly, daily, weekly, monthly, seasonal, or yearly requirement, its damage
cadence is respectively 600, 3,600, 21,600, 86,400, 259,200, or 1,209,600 seconds.
Month/season/year requirements use recorded campaign month boundaries rather
than a fixed 30-day month. B130's season remains exactly three months. Calendars
may be extended while dependencies are active, but their existing boundaries
cannot be replaced underneath a pending obligation.

`AdvancePhysiology` advances through every due harmful/contact interval in one
transaction; `SettlePhysiology` resolves a specific persisted identity at its
exact deadline. Ordinary resource advancement cannot jump that deadline. An
ended exposure, a fresh dose, or changed binding cannot erase already-owed harm.
An approved purchase replacement is reconciled before its next deadline, taking
effect at the reconciliation clock without retroactive injury. The ordinary
advancement path requires any due interval to settle before buying off or
changing the purchase. Missing purchases retire future intervals. Current GM
membership and deployment trust are checked again on commits and retries.

Weakness bypasses DR and injury-tolerance wound reductions through internal
canonical injury, while the normal injury reducer still resolves shock,
major-wound knockdown/stun, survival checks, and death. Actual death retires the
harmful schedule. Ready hand-held items are passed to the injury reducer for
its existing drop/unready consequence. Private observations, purchases, and
dice stay off player projections and event streams. Exact request retries
return their original receipt; alternate IDs cannot spend an interval again.

`tests/test_harmful_physiology_persistence.py` exercises actual approved builds,
all unmodified frequencies, missed versus fresh doses, interrupted contact,
month-end boundaries, death and major wounds, ordinary-clock guards, current
build changes, authority races, same-ID and different-ID database races,
rollback, restart, private projections, and seeded re-execution on SQLite and
PostgreSQL. `tests/test_physiology_traits.py` retains the lower-level formulas
and canonical injury assertions. These are executable subset evidence, not a
claim of full Basic Set certification.

Unsupported: Dependency Aging; Weakness Fatigue Only and Variable intensity;
multiple distinct purchases of the same disadvantage; inference of a specific
substance from unstructured narrative; and unrecorded earlier exposure. Their
construction/runtime rejection is retained. Harmful physiology is not admitted for actors with any authored transformation
rules, including native actors who have not yet transformed. Rules migration
also refuses to introduce such rules while a harmful binding is live. This
explicitly unsupported combination is rejected before any schedule or migration
is written. The boundary prevents a reproduced ordering failure: a timed
one-minute Weakness body could revert at its injury deadline to a different
approved frequency before that injury was settled. A late checkpoint guard
would risk rolling back the clock itself. Normal transformation campaigns with
no harmful schedule remain unchanged; the tests exercise actual timed expiry
and both atomic admission refusals. General ordering of harmful injury with
forced/timed body changes remains unimplemented, and is not certified here.

Lifecycle regressions also cover re-purchasing a retired trait without losing
its recorded exposure, admission checks during reconciliation as well as first
observation, death from another injury followed by a clock advance past the old
deadline, and repeated contact-satisfaction cycles. Completed contact consumes
all accumulated partial credit. Retiring a dead subject does not require a
current build approval; living injury still does. An unrelated actor awaiting
approval does not block another subject's physiology or calendar declarations.
