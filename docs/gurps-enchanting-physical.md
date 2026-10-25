# Physical lifting and jumping during enchanting rest (#1013)

The uploaded Campaigns fourth printing, SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`,
B481 says a mage who is not actively working on an enchantment has no enchanting
disadvantage. B353 gives a two-handed lift a four-second duration and an 8×Basic
Lift limit. A ST 10 actor has Basic Lift 20 lb and can therefore raise 160 lb in
four seconds. The existing physical reducer supplies these limits, optional
Lifting checks, ordinary injury/exertion checks and durable route progress.

Fresh Lift and Jump commands now check their complete source-derived duration against the
existing Slow and Sure work calendar before injury, exertion or Lifting dice.
The duration comes from the selected native physical operation, independently
of any later skill-roll result. B352 gives prepared jumping two consecutive
Concentrate maneuvers plus a Move; unprepared jumping uses one Move and halves
the distance. The current short Jump consumer therefore takes three seconds
when prepared and one when unprepared. Enrolled mages must have idle concentration. Work at
an active shift or continuing into the next shift refuses before costs or clock
advancement. An activity ending exactly at the next shift remains legal. The
project's credited energy, mage-days and completion deadline do not change.
Normal physical costs and limits still apply; this path grants no FP, rest,
additional shift or concentration exemption.

The trusted scenario resolver supplies the actual physical route. The private
command envelope captures that typed route with admission generation 1; the
player's command still contains only a route ID. Current state, ownership of the
actor, readiness, route location, physique and activity eligibility are checked
inside the ordinary transaction. A concurrent state revision makes stale intent
fail. Exact retries reuse the recorded trusted route without consulting the
resolver again. Seeded reexecution consumes the capture through the registered
physical command adapter, which never invents absent geometry. Historical
unmarked commands retain their old admission and payload, including the old
refusal while enrolled in enchanting. Old commands without a captured route
still cannot acquire trusted geometry from generic offline replay.

`tests/test_enchanting_physical_nighttime.py` starts real Staff construction from
its original authored genesis and checks lifting at 28,800 and at 86,396 seconds.
Both cases take four seconds, record 160 lb capacity and completed physical
progress, and preserve the active project's exact deadline of 1,238,400 seconds.
HP and FP amounts remain unchanged. A 161 lb attempt fails at the same actual
capacity and takes four seconds without recording completed progress. Active
work at 0 or 86,400, and an attempted 86,397 start, refuse without a clock or
state change. Tests also cover authority, stale revision, pending concentration,
checkpoint rollback, concurrent exact retries, restart, strict private capture,
historical admission, stream folding and original-genesis seeded reexecution on
SQLite and PostgreSQL.

Additional prepared and unprepared Jump cases start at 86,397 and 86,399 and
finish exactly at 86,400. With Basic Move 5, the native standing broad-jump
capacity is 7/3 yards when prepared and 7/6 when unprepared. A two-yard route
therefore records completed progress only in the prepared case; both use their
actual time and retain unchanged HP/FP and project work. Both paths cover
concurrent exact retry, restart and original-genesis seeded reexecution on both
stores. A prepared start at 86,398 refuses before entropy or clock advancement.

This is bounded Lift and Jump evidence. Other physical procedures, physical scene travel,
combat and ceremonial nighttime consumers, active-work casting and grouped
project clocks remain separate #1013 work. No source-ledger or certification
status is promoted.

## Individual Swimming during rest

The same captured-route admission now includes the existing individual Swimming
procedure. B354 gives humans water Move equal to Basic Move/5, rounded down with
a minimum of 1 yard/second, modified by encumbrance. B355 calls for a fatigue
roll each minute at top speed, against the higher of HT or Swimming; failure
costs 1 FP. B354 makes an initial intentional entry +3, then requires a separate
five-second retry cadence after a failed Swimming roll and a 1 FP loss. These
rules remain the native physical and hazard reducers' responsibility.

Before any injury, exertion, Swimming or fatigue dice, the fresh admission reads
the current route's matching prior progress and elapsed time. Its planned step
is `min(authored_seconds, 60 - elapsed % 60)`, with authored seconds restricted to
1–60. The entire step must fit between enchanting shifts, even if a failed check
would finish sooner. A route authored for 40 seconds therefore performs a
40-second first step and a 20-second second step at the next minute boundary.
Changed route geometry, existing distress, a hazard deadline crossed by the
planned step, pending concentration and active work refuse before entropy. The
source actor/locomotion preflight still runs before fetching fresh trusted route
facts, including Rooted Feet refusal. Recorded retries reuse their old capture.

This rest lane changes physical progress in the current scene. Enrolled mages
cannot use it for group hiking, scene exits or destination changes. Split-party
concurrency remains governed by the existing synchronous guard. The rest
admission does not add a second work shift, cure fatigue, clear drowning or grant
extra project progress. Historical unmarked swimming retains its prior admission
and serialized envelope.

`tests/test_enchanting_swimming_nighttime.py` observes a real 60-yard,
60-second swim by an approved Basic Move 5 mage during an active Staff project's
rest. The initial default Swimming 6 gets +3 for intentional entry; an actual 9
succeeds. The actual minute-end 11 fails against HT 10 and spends 1 FP. A step
starting at 86,340 may finish at 86,400; starting at 86,341 refuses. Both stores
also exercise the 40+20-second boundary using persisted elapsed time.

A failed native Swim step consumes its existing one-second simulation interval,
loses 1 FP and creates the actual drowning schedule with its next check five
seconds later. This one-second physical-step convention is the existing native
host behavior; this rest adapter does not claim it is a separately printed
B354 duration. Tests observe further failed drowning checks costing another FP,
then actual recovery changing the next check to 60 seconds, followed by a
successful recovery check changing it to 300 seconds. Shared-clock commands
cannot skip these obligations. The tests inspect the actual FP, HP, hazard stage,
clock and unchanged enchanting project, not a synthetic successful receipt.

Native drowning continuations now capture the committed failed Swim command's
input, input digest and result, together with the exact current hazard schedule.
Only the command log can supply a fresh producer capture. Inside the transaction
and on replay, the source input's private route, failed physical result and
persisted feat event must agree with the actor, source time and derived drowning
identity. The captured schedule must exactly match the current one, including
deadline and cycle, before dice. Forged or altered producer input, route, result,
digest, actor, cycle and deadline refuse. Existing natural outside-exposure
continuations retain their separate source contract. No new public command field
or genesis origin record is introduced.

The new tests cover physical and hazard checkpoint rollback, authority and stale
revision, concurrent exact retries, restart, active distress and future hazard
boundaries, and complete original-genesis seeded reexecution of successful
swimming, fatigue, failed entry, failed drowning checks and actual recovery on
SQLite and PostgreSQL. Both paths continue to use the canonical health and clock
consumers. Broader climbing/fall timing, hiking mileage, scene return obligations
and grouped enchanting clocks remain separate unverified work. No source-ledger
or certification status is promoted.

Fresh voluntary physical skill checks also use the independent [minimum skill
admission](gurps-physical-skill-minimum.md). It preserves historical rest captures
and excludes involuntary Swimming and mandatory fatigue/drowning checks.
