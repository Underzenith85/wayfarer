# Entering enchanting work with another concentration pending

Campaigns fourth printing B481 defines a mage-day as one full eight-hour workday,
not an eight-hour quiet-rest requirement. Characters third printing B236–237 and
Campaigns B366 make ordinary casting a Concentrate activity, including a
high-skill spell whose personal FP cost is zero. Characters B69 likewise requires
one second of concentration for the native Mind Reading attempt.

Fresh `BeginEnchanting` now checks the canonical cross-service concentration
guard for every participating enchanter before work creation or energy payment.
This covers both enchanting methods and the same Begin operation used to resume
an interrupted project. A pending ordinary Light or native ability concentration
must finish or cancel first. Already-active nonconcentrating ordinary effects do
not become new casting activity. Existing pending/held Melee-spell restrictions
remain in place.

An independent, strict private `enchantment_work_admission_generation: 1` records
this admission policy only on Begin. Absence preserves historical accepted work,
command bytes, exact retries and seeded reexecution, even where an old record
accepted overlapping concentration. Fresh actor/GM authority and stale revision
checks still use the ordinary transaction pipeline. No public command fields,
new rest-credit ledger or automatic cancellation were added.

`tests/test_enchanting_concentration_admission.py` exercises actual approved
zero-FP Light and Mind Reading producers for the lead and assistant, Slow and
Sure/Quick and Dirty costs, cancellation, active effects, resume, authority,
strict metadata, exact retry and original-genesis seeded reexecution on SQLite
and PostgreSQL. The active-work constructor is `_begin`; no separate join/start
constructor was found in the implemented project vocabulary.

The same work-admission policy interrupts existing real FP-rest recovery tasks
for participating enchanters when work is actually active. It preserves other
actors' rest and non-rest medical tasks. Scheduling future work preserves rest
that ends by the next shift and refuses conflicting rest before creating work.
Failed admission leaves tasks, FP and time untouched.

Fresh exact-Basic-Set medical rest has a separate private
`medical_rest_generation: 1`. It checks the complete native interval against the
work calendar before querying rest facts or drawing dice, and checks again under
the transaction lock. Off-shift rest ending exactly at a work boundary is lawful;
completion can settle its earned recovery at that boundary. This does not require
a new FP ledger. Actual FP5/rest600/Begin/Advance600 witnesses retain FP5 under the
new work policy, while unmarked historical Begin retains FP6 on reexecution.

The narrow medical replay adapter accepts only marked FP-rest starts with their
complete, strictly validated captured CareEnvironment, and marked completions
bound to an existing rest task. It never calls an unrecorded resolver or supplies
default facts for legacy/non-rest medical procedures. This does not claim replay
coverage for the whole medical service. Exact committed retries retain their
original receipts even after work has interrupted the rest.

Nighttime ability admission remains unsupported pending complete interval
preflight across shared clock hosts. No eight-hour quiet-rest obligation was
introduced: the calendar remains an availability rule for the eight-hour workday.

Medical execution retains its existing self-actor control contract: the authenticated
principal must equal the command actor. A distinct principal controlling that actor
is unsupported by this medical service; the narrow captured-rest replay adapter
does not expand that contract or claim whole-service medical replay.
