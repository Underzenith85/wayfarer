# Luck: ordinary task consequences (B66, #866)

Sources checked: supplied *Characters*, fourth edition, third printing B66;
*Campaigns*, fourth edition, fourth printing B346–347 and B426. Source files
remain private. These references support the numeric rules and timing below.

## The persisted consumer

`orchestration.tasks.TaskService` runs ordinary `Inspect`/`Social` CheckRule
outcomes and authored B346 long tasks through the existing campaign transaction.
Its engine-only commands do not add an API, UI, voice operation or public schema.
Task definitions, current approved builds, usable equipment, scenario facts,
conditions and source modifiers determine targets. Players submit identities and
choices, never target numbers, original dice or approval records.

The host pays prerequisites and advances actual campaign time before opening a
server-generated original. Inspection first observes B426 continued exertion;
a failed Will check at nonpositive FP persists collapse without opening the task
roll. Accepted ordinary profile fatigue is charged once through the actual FP/HP
ledger. Recovery and concentration cannot continue through work.

An original is persisted without its dependent task consequences. The actor or a
trusted seated GM can accept it; an eligible actor can instead spend their own
Luck. Both rerolls use the transaction's recorded random stream. The selected
check, Luck receipt and actual knowledge/progress consequences commit together.
Failure or rollback commits none of that choice. Accepting a captured check does
not recalculate its original target, restore old state, or repeat elapsed time.

B66 permits the decision before anyone's next roll. The campaign therefore holds
later consequential commands until the pending choice is accepted or replaced.
A trusted GM can accept the original when its player is absent. Clock pause and
resume do not draw dice. No normal checkpoint or NPC roll runs after a new
original opens; prerequisite checkpoints run first. A following task phase
refreshes its current actor, equipment and conditions before its own original,
without rewriting any earlier selected check.

## Long tasks

The private phased adapter records supervisor overtime HT, supervision,
worker overtime HT, and the worker skill check separately, where applicable.
Each actor controls only their own pending check and Luck. Supervision uses the
GM-bound Administration or Leadership skill of a separate actor who spends the
shift coordinating. Success gives workers +1, critical success +2, and failure
adds no bonus.

More than eight hours requires HT, at -1 per hour beyond ten. Failure penalizes
the later skill by the worse of -2 or the HT margin, and pays the corresponding
FP through the canonical fatigue/injury reducer before any following original.
Critical HT failure also records an enforced next-day work restriction. Current
fitness and other check modifiers retain their source traces.

For an eight-hour contribution, success earns 8 hours, critical success 12,
failure 4, and critical failure none. Only a selected critical failure rolls its
additional 2d of destroyed prior labor. Destruction stays deducted on later days.
Successful overtime HT scales noncritical work to the actual duration. After
failed HT, extra labor requires a successful worker check: the literal B346
branch reading gives 4 hours on a ten-hour double failure, versus 5 hours when
HT succeeds but the worker check fails. This is an interpretation of those
conditions, not a separately printed numeric example. A work day is an
explicit 24-hour interval anchored at the shift's start; one daily contribution
per worker or supervisor prevents repeated short commands from bypassing overtime.
The exhausted next day is the following full interval, including midnight-crossing
shifts. The GM authors the required labor and applicable time-spent modifier.

## Real play time and authority

Ordinary Luck costs 15 points and has a 3,600-second cooldown; Extraordinary costs
30 and has 1,800 seconds; Ridiculous costs 60 and has 600 seconds. The next deadline
starts at actual use. Campaign time, crossing a clock hour and unused intervals
do not provide extra uses.

A campaign-wide clock records trusted elapsed play time from captured command
instants, with actor-specific cooldown deadlines. It begins paused and a trusted
GM starts or pauses it. Integer microseconds preserve fractional boundaries;
backward instants reject. Eligibility is also checked at the original roll's
captured time, so waiting over a cooldown while looking at a pending result cannot
make an originally unavailable use legal. Replay uses the recorded instant and
seed, never a new wall-clock reading.

Current campaign control and trusted GM seating are checked again under the lock
and on exact retries. Stale revisions, changed payloads and post-commit choices
reject. Private task, Luck, clock and phase records cannot be supplied at genesis.
Secret task dice and state events remain GM-only. A secret original can be accepted
by the GM but cannot gain after-the-fact Luck; secret predeclaration belongs to
#871. Ordinary actor-local results do not expose another actor's private roll.

## Evidence and remaining source scope

`test_task_host`, `test_task_host_boundaries` and `test_task_host_work` exercise
real SQLite/PostgreSQL stores, current control, rollback, independent-store races,
restarts, complete event folding and seed-only command reexecution. Numeric
oracles cover world-fact changes, exact labor, overtime FP, ruined progress,
separate supervisors, fresh later-phase conditions and microsecond cooldowns.
`test_prepared_task_checks` preserves old ordinary histories and rejects forged
selected traces. `test_long_task_phases`, `test_real_play_clock`, and
`test_real_play_command_time` cover their lower-level source boundaries.

The earlier `apply_lucky_task` and cinematic helper are reusable reducers, not
independent proof of a persistent host. Their historical call behavior is retained.
The whole Luck family remains partial under #854. Existing owners remain #855
for Active/Aspected/Defensive consumers and #867–871 for damage, reaction,
attacker, outside-event and secret predeclaration integrations. Supported purchase
availability is separate from certification completeness.

This consumer also explicitly refuses NPC overtime without a source-bound prior
Influence agreement, rather than assuming NPC consent. That remaining prerequisite
adapter is recorded under #854; this change does not certify every B346 procedure,
other task families, other Luck consumers, or the whole engine. The source ledger
and generated inventory retain the same partial disposition and live owner.
