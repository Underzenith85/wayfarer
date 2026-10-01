# Cyclic engine execution

Issue #761 uses the supplied Characters third printing, printed B103–104. The
source specifies an initial occurrence plus the purchased remaining cycles,
preserves penetration effects, lets successful resistance end the attack, and
prevents recovery of its damage while it remains active. Source files match the
existing selected-source ledger SHA-256; no source prose is copied here.

An approved Innate Attack purchase can carry typed `attack_modifiers` for Cyclic
and optional Resistible. Construction checks damage kind, interval, cycle count,
stop condition, and computes the repeat percentage (halved for Resistible before
adding contagious cost). The existing trait channel remains a trusted authored
attack context. Players cannot supply damage or a forged Cyclic profile through
the attack command.

Independent exposures persist separate occurrences and per-cause HP/FP healing
debts. The resource clock executes due damage chronologically using its injected
command RNG. The trusted `StopCyclic` reducer accepts only the named stopping
condition for the subject's active occurrence, checks revision and preserves
exact command retries. Injury and fatigue continue through their canonical
reducers. General recovery honors each active cause's debt.

This PR covers the noncontagious attack with ordinary DR penetration and optional
HT-based resistance. It does not promote the broader modifier certification row:
contagious exposure is #762, Symptoms is #763, and combinations with other
penetration modifiers remain visibly unsupported. Existing source certification
and end-to-end compliance gates are separate from these engine regression tests.
