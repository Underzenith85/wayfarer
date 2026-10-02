# Luck: persisted task and reaction consequences (B66)

Sources checked: supplied *Characters*, fourth edition, third printing B66;
*Campaigns*, fourth edition, fourth printing B346–347, B426, B494–495,
B508, B518–519 and B560–561. Source files
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
Secret task dice and state events remain GM-only. The historical
`BeginTaskCheck(secret=True)` still draws its original immediately and cannot gain
after-the-fact Luck. Its command, pending and result encodings are unchanged.
Ordinary actor-local results do not expose another actor's private roll.

## Secret predeclaration

`PrepareSecretTaskCheck` lets a currently seated, trusted GM prepare an exact
unrolled Inspect or diplomacy Social opportunity after its actual prerequisites.
The separate `secret-unrolled` pending record binds the campaign, actor, canonical
configuration, captured target and modifiers, and preparation time. Preparation
uses no target dice; prerequisite exertion and clock consequences still run first.
The controlling owner can read only the opaque opportunity identity and generic
status. No check, Luck attempts, target, margin or action outcome is exposed by
that pending read, the owner's result, campaign/model projections or actor events.
Knowledge legitimately learned from the selected result follows actor perspective.

`ChooseSecretTaskCheck(choice="use-luck")` requires current control of that actor,
including under the transaction lock and on retries. A trusted GM seat alone
does not authorize spending a player's Luck. The declaration rechecks current
approved Luck and the exact prepared context before the host privately draws three
complete attempts. It selects the lowest total (earliest on ties), scores the
captured target, and commits the selected world consequences, receipt, cooldown
and closed opportunity in one transaction. There is no parked reservation.

Unrolled preparation time is not an original-roll eligibility timestamp: an
opportunity prepared during cooldown may be declared when the exact deadline
arrives. Paused real time does not accrue. Existing rolled opportunities retain
the original-time guard described above. A stale current target or lost eligibility
refuses before target dice, without spending Luck or destroying the opportunity.

A trusted seated GM can instead choose `resolve` for one ordinary secret attempt
or `cancel` before any target dice. Cancellation needs no current build approval,
spends no Luck, and retains paid time and fatigue. Both close the global pending
gate. The same gate remains a host serialization policy, not an additional B66
rule. Secret long-task phases are outside this bounded Inspect/Social consumer.

## Secret reaction continuations

`PrepareReaction` binds an unrolled reaction to its actual source and the actor
receiving it. A trusted seated GM supplies that source; the controlling owner
can declare their own Luck without seeing the secret reaction. Preparation may
settle a preceding hireling search or Diplomacy contest, but never the reaction's
target dice. The immutable prerequisite remains fixed when the later reaction
is selected. An already committed immediate reaction cannot be reopened.

The reaction uses B494's high-is-good 3d total with its source modifiers. A Luck
declaration draws three complete reactions and chooses the highest total, with
the earliest attempt winning a tie. Uncertain Reputation recognition is resolved
once and reused by all three; Diplomacy retains recognition captured with its
preceding contest. The declaration revalidates authority, current source context
and cooldown before any remaining recognition or target entropy. The same task
clock and actor cooldown cover ordinary checks, secret checks and reactions.

The private continuation names the actual effect it resumes:

- Canonical social and authored NPC interactions use the existing disclosure
  policy and `World.learn`; NPC occurrences also settle their decision and budget
- Both Diplomacy entry routes preserve their preceding skill/Will contest and
  combine its fixed result with the selected fallback reaction
- Initial hireling loyalty becomes the selected reaction total in its persisted
  contract, after the separate search succeeds
- A qualifying rescue uses B519's +3-or-more reaction and, on Good or better,
  retains the greater of prior loyalty and that total; an explicit GM permanent
  injury/death bonus is a separate source decision
- A reaction-mode law procedure changes its bound case state and consumes its
  authored time once; Administration changes its explicitly bound knowledge

The [campaign continuation rules](campaign-reaction-continuations.md) specify
recipient identity, standing, recognition, rescue and law adjudication limits.
A reaction band alone does not imply surrender, payment, aid or an unimplemented
relationship state. Information disclosure uses an authored outcome policy: a
simple answer can include Neutral, while a complete complex answer can require
Good. It is not a universal Good threshold for every information request.

The selected reaction, actual consequence, recognition memory, Luck receipt and
cooldown commit in one transaction. A trusted GM can instead resolve one ordinary
reaction or cancel before target dice. Cancellation preserves committed
prerequisites and closes the original source identity so another entry point
cannot reroll that search or contest. Current authority is checked on retries;
secret projections reveal neither candidate dice nor private source facts.

## Evidence and remaining source scope

`test_task_host`, `test_task_host_boundaries` and `test_task_host_work` exercise
real SQLite/PostgreSQL stores, current control, rollback, independent-store races,
restarts, complete event folding and seed-only command reexecution. Numeric
oracles cover world-fact changes, exact labor, overtime FP, ruined progress,
separate supervisors, fresh later-phase conditions and microsecond cooldowns.
`test_secret_task_host` and `test_secret_task_boundaries` cover the unrolled
lifecycle on both stores: target 12 with attempts 18/15/7 selects 7 at margin +5;
seed `ab` repeated 32 times produces totals 15/7/9 and selects 7. All-failure learns
no clue while spending Luck, critical success and equal-total ordering are checked,
and preparation/cancel, ordinary resolve and declaration consume zero, three and
nine target faces respectively. Evidence also covers exact microsecond deadlines
for all three tiers, pause/resume, paid-cost preservation, current control/source
changes, competing independent stores, rollback after a candidate commit,
restart/exact retry, complete event folding and seed-only reexecution of all three
continuations. `test_prepared_task_checks` preserves old ordinary histories and
rejects forged selected traces. `test_long_task_phases`, `test_real_play_clock`, and
`test_real_play_command_time` cover their lower-level source boundaries.

Reaction evidence is bound to `test_reaction_task_host`,
`test_reaction_task_boundaries`, `test_reaction_task_routes`,
`test_reaction_recognition_host` and `test_reaction_campaign_host`, with independent
source oracles in the prepared-reaction, prepared-Diplomacy and campaign
continuation suites. These assert actual knowledge, loyalty, case and occurrence
state alongside timing, authority, cancellation, rollback and replay.
`test_reaction_campaign_traits` covers current approved standing and once-only
recognition; `test_social_captured_replay` covers immutable immediate-source
capture, seed reexecution and live GM trust changes during commit and retries.
The [reaction host contract](reaction-luck-host.md) maps the complete bounded
consumer and its source limits. Historical immediate-social inputs without a
captured resolver source retain exact retries and event folding, with their
seed-only replay limitation stated explicitly.

The earlier `apply_lucky_task` and cinematic helper are reusable reducers, not
independent proof of a persistent host. Their historical call behavior is retained.
The whole Luck family remains partial under #854. Existing owners remain #855
for Active/Aspected/Defensive consumers, #867 for damage, #869 for attacker rolls
and #870 for outside-event integrations. The bounded #868 reaction consumer joins
the ordinary #866 and secret #871 task consumers; this does not promote those
remaining source contracts.
Supported purchase availability is separate from certification completeness.

This consumer also explicitly refuses NPC overtime without a source-bound prior
Influence agreement, rather than assuming NPC consent. That remaining prerequisite
adapter is recorded under #854; this change does not certify every B346 procedure,
other task families, other Luck consumers, or the whole engine. The source ledger
and generated inventory retain the same partial disposition and live owner.
