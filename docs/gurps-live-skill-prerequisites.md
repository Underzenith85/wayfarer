# Purchase prerequisites in live skill checks

Characters B168–174 distinguishes skill acquisition, defaults and prerequisite
traits. B96's selected Unusual Background admission and B235/B480's approved
spell-learning requirements depend on facts in the purchased build. Live
equipment and condition recomputation must retain those facts rather than
silently treating the approved character as having no prerequisite advantages.

The previous ordinary target path recompiled every purchased skill with an empty
default context. An approved mage could therefore fail an unrelated Observation
check because Magery disappeared during that recomputation. A GM-approved paid
background could similarly lose its admitted trained skill or default.

Fresh Basic Set public Inspect/Social and ordinary, secret and long-task host
commands capture private `skill_prerequisite_context_generation: 1`. The engine
receives an explicit preparation flag and reuses
`CharacterCompiler.default_skill_context(build.purchases)`. That context contains
the exact purchased definitions and amounts, selected skill TLs, campaign TL,
supported capability witnesses and selected campaign defaults. It does not infer
equipment, biographical facts, an unpaid background or missing training.

Public preview, minimum-skill admission and actual resolution use the same
prepared context. Task admission and preparation after elapsed time use it too,
as does unresolved long-task target refresh. Already rolled task originals keep
their captured preparation. Secret unrolled choices retain their preparation's
generation in a private, strictly validated field; a later fresh selection does
not change a historical pending choice's semantics. The absent field remains
omitted from historical serialization. Task records remain forbidden at authored
genesis and are not accepted through public command input.

Historical command absence selects the previous computation. This preserves
exact retry and seeded reexecution rather than changing prior approved defaults
or interpreting old receipts under new purchase facts. The existing power
approval command also has a registered replay adapter, which reruns its ordinary
GM authority checks; approvals are not injected into the execution proof.

`tests/test_live_skill_prerequisite_context.py` exercises a lawful approved mage
without enrolling in an enchanting project, keeping the prerequisite defect
separate from project scheduling admission. Its actual Observation is 17, tool
quality adds 1 and the authored task subtracts 2, producing a target of 16 and a
real discovered fact after ten seconds. The Enchant college witnesses in that
fixture are test-only learning prerequisites and do not certify those spells.
Other tests cover real paid-background GM approval, trained/default Inspect and
Social checks, unpaid refusal and forbidden catalog admission, actual staged and
secret outcomes, one man-hour of selected long-task progress, strict private
generation boundaries and current/historical replay on SQLite and PostgreSQL.

This correction supplies missing approved facts; it does not relax prerequisites,
reprice traits, support new background kinds or promote certification status.
Other independent target consumers still require their own source and generation
review.

## Deferred party and ruling consumers

Queued ordinary checks, queued noncombat approaches and approved ruling
execution also use the same compiler context. Their queue/request command
captures the generation in its private envelope and writes a bound private
resource record. The record survives deferred execution without adding fields
to public queued activities or rulings. A different subgroup's later activity
may flush a queue, but its generation cannot upgrade an older queued check.
Likewise, a fresh ExecuteRuling command cannot change an already approved
historical ruling's generation. Absence retains the original computation.

These records are rejected at authored genesis, strictly bind producer kind,
command and actor, and are absent from player campaign projections. Deferred
resolution still recomputes the actor's current approved build, equipment and
condition modifiers; the captured choice is the interpretation generation,
not a frozen numerical skill or a substituted character sheet. Ruling modifiers
remain the exact selected server-authored alternative. The registered ruling
replay adapter reruns requests, decisions, execution and internal policy
evaluation with their original authority boundaries.

`tests/test_deferred_skill_prerequisite_context.py` proves actual unenrolled mage
grouped Inspect and noncombat outcomes, paid-background grouped Social, manual
and automatic ruling approval through actual consequences, old queued and
approved-context preservation, restart and original-genesis seeded reexecution
on both stores. Private records cannot be supplied through authored genesis or
exposed in ordinary player views. These tests do not admit grouped enchanting
project clocks or a previously unsupported nighttime activity.

Long-task phases already retain their captured `ActivityActor`, target levels,
modifiers and selected prerequisite traces. The task host re-prepares only an
unresolved phase under its captured command generation. There is no autonomous
long-task skill recompilation when an ordinary Wait advances the clock; pending
choices are protected by the existing task boundary. After selecting one hour
of actual work, a subsequent lawful Wait advances time with no dice and grants
no extra labor. This is a boundary check, not a new automatic work scheduler.
