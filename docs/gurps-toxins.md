# Basic Set toxins and drugs

Issue #518 adds a persistent, authoritative toxin family for the selected
Campaigns fourth printing. It does not own disease profiles or contagion; those
remain a separate illness family.

## Poison profiles and delivery

`ToxinProfile` records delivery vector, delay, resistance modifier, damage and
effect cycles, interval, condition duration, treatment owner, and dependency
class. `DeliveryEvidence` is trusted scenario evidence, not a player payload.
Contact, blood, digestive, respiratory, sense-based, and follow-up delivery each
validate the physical exposure that actually occurred. Protection and a
non-penetrating follow-up fail closed (B437-B438).

Each dose creates its own `ToxinExposure`. Power-of-two dosage scaling shortens
delay and cycle interval, multiplies consequences, and penalizes resistance.
The due cycle is settled by an explicit command, with a receipt and durable
result. Resistance terminates later cycles; an appropriate treatment changes
only later checks, while a profile-bound antidote can halt later cycles. Nothing
edits injury or fatigue already committed (B438-B439).

Authoritative checkpoints retain substance identity so replay can continue,
but `project_toxin` reveals it only to actors recorded by an authorized
discovery procedure. Other observers receive an identity-free view.

## Alcohol, dependency, and overdose

`Intoxication` retains the hourly drink count, session total, progressive
intoxication level, stop time, sober checks, and delayed hangover. Hourly checks
use the higher of HT or Carousing and explicit meal/tolerance context. Failed
checks advance the persistent condition; severe results retain the secondary
purge outcome, and an alcohol coma requires medical care (B439-B440).

`DrugDependency` records physiological or psychological withdrawal. Daily
checks are capped at 13 and require 14 successes. An available drug on failure
records a dose and resets progress. Without it, physiological withdrawal commits
1 HP and blocks natural healing of that debt; psychological withdrawal records
progressive quirks. Success or an explicit abandonment releases the healing
restriction (B440).

Multiple depressant doses use the poison dosage rules. A critical resistance
failure records hours of unconsciousness and a durable overdose schedule. The
provided Basic profile applies 1 toxic HP every 15 minutes for up to 24 cycles;
ordinary persistent injury thresholds own coma and death (B441).

## Determinism

All reducers require server authority, expected resource revision, explicit
randomness, exact deadlines, and command receipts. Repeating a committed command
returns its saved result without consuming randomness. Resource clock advances
cannot cross an active toxin or withdrawal deadline.
