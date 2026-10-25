# Minimum effective skill for authored noncombat approaches

The supplied Campaigns fourth printing, B345, prohibits nondefense success
attempts below effective skill 3. This applies after the task's modifiers; a
very low target does not become a permitted attempt merely because 3 or 4 would
otherwise score as a critical success. The printed defense exception remains
separate.

Fresh Basic Set `approach_noncombat` commands capture the private
`minimum_effective_skill_generation: 1`. After deriving the actual purchased
skill and equipment effects, admission sums the authored task modifier and
current condition modifiers exactly as used by the resulting check. A final
target below 3 refuses before dice, fatigue/HP costs, elapsed time, encounter
progress or revealed facts. Target 3 remains a lawful attempt with the existing
success and critical rules.

The generation is independent of the existing enchanting-rest admission. Older
unmarked approach commands retain their original inputs and behavior, including
commands already carrying the enchanting-rest generation. Exact retry and seeded
reexecution use the captured choice. No public fields or shared scoring rules
change; this bounded correction does not change mandatory health checks or the
low-target defense exception, and does not claim every ordinary action consumer
has been audited for this boundary.

`tests/test_noncombat_minimum_skill.py` verifies final target 2 refusal and target
3 acceptance with positive/negative equipment and authored modifiers, actual
cost/time/discovery consequences, authority and stale revision checks, concurrent
exact retry, restart, event folding and original-genesis seeded reexecution on
SQLite and PostgreSQL. Invalid recorded generations fail closed. This source
correction does not promote the broader success-roll certification capability.

The same independent generation also covers fresh public `Inspect` and `Social`
commands in the Basic Set profile. Preview and execution preflight use the
engine's existing prepared task context, including authored darkness, equipment
and current condition modifiers. An insufficient target returns
`check.minimum_skill` without writing a receipt or changing the campaign; the
transaction independently rechecks admission before the engine can interrupt
concentration, spend resources, roll or advance time. Successful target-3 checks
retain ordinary discovery and elapsed-time consequences. Historical typed-action
envelopes without the generation continue to execute their recorded semantics.

`tests/test_action_minimum_skill.py` covers both public action kinds, a defaulted
Observation skill, authored darkness, real fact discovery and time, refusal
without entropy or writes, concurrent exact retry, restart and original-genesis
seeded replay in SQLite and PostgreSQL. These ordinary public checks retain
their existing zero action-fatigue contract; nonzero profile fatigue requires
the separate exertion adapter. Staged task hosts require their own admission
review and are not certified by these public-action tests.

## Queued activities and approved rulings

Fresh Basic Set queued check and ruling-request producers capture the independent
`minimum_effective_skill_generation: 1` and a bound private
`minimum-effective-skill:` resource record. An earlier prerequisite-context
generation does not imply this floor. Existing queued activities and approved
rulings without the new record retain their historical computation, including
when a fresh command later flushes or executes them. No fields are added to
public queued-activity or ruling records.

An initially inadmissible queue refuses before recording its work interval,
rolling, spending action resources or advancing shared time. At each due-time
batch, all floor-enabled checks are prepared without effects before the first
queued action die. Invalid activities receive rejected receipts while other
legal work commits. Each activity checks again immediately before resolution,
so an earlier activity cannot silently invalidate a later target. An illegal
check never rolls or pays its action costs.

Due-time preparation uses the authoritative state after scheduled world-clock
consequences. This does not predict future attributes or erase independently
authorized clock, hazard, NPC or other actors' consequences. A check that becomes
inadmissible during elapsed time is rejected at its due time; the already
authorized shared clock still advances. The tests distinguish this case from an
initially illegal attempt that spends nothing.

Ruling requests remain roll-free statements of intent; a selected positive
alternative may make an otherwise too-low ordinary target usable. Execution
validates the current final target, including the exact approved server-authored
alternative and current conditions. The engine's shared canonical ruling helper
prepares those modifiers for both admission and actual scoring. Neither a client
number nor a duplicated host formula supplies the ruling adjustment. An invalid
execution refuses before dice, interruptions, costs or time advancement.

Queued ordinary checks use the ordinary engine preparation. Queued noncombat
approaches use the same pure preparation as their actual approach scorer, without
changing their existing purchased-skill and modifier contract. These sources
remain distinct where the existing architecture gives them different inputs.

`tests/test_deferred_minimum_skill.py` proves literal final targets 2 and 3,
equipment modifiers, actual toxin-induced Drowsy conditions, positive/negative
selected ruling modifiers, real legal outcomes, source admission and private
genesis/privacy boundaries. An instrumented real engine and RNG prove that the
valid-first/invalid-second batch checks both targets before its first die and
retains the legal actor's committed work. Independent seeded proofs, historical
context-only producer retries and restarted exact retries run on both stores.
No broad success-roll or deferred-family certification status is promoted.
