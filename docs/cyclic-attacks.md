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

The initial channel carries the trusted already-resolved basic-damage amount, as
in the existing trait attack adapter. Each later occurrence draws the purchased
Innate Attack dice again from the command RNG. B103 calls for normal damage at
each interval; B61 defines purchased levels as damage dice. The engine therefore
does not reuse the first rolled total as a permanent fixed amount. Partial-die
purchases remain unsupported by the existing integer-level character binding.
Each occurrence retains its damage dice and resistance CheckTrace in the private
resource event ledger; public projection and certification remain separate.

For composed delivery, Campaigns fourth printing B378 halves the initial basic
damage at or beyond the approved 1/2D range and rounds down before DR. A one-point
roll can therefore become zero. HP and FP remain unchanged on that initial
delivery, whose result is unaffected and retains its roll evidence. B103 ties
repetition to exposure, so a successfully delivered zero-damage hit still schedules
the remaining cycles with zero initial recovery debt. Later cycles reroll the
purchased dice and can cause injury. A private runtime occurrence accepts zero
initial damage only alongside its required positive damage-dice expression;
missing or zero future dice remain invalid. The existing positive-damage authored
models and frozen scenario/authoring/social schemas are unchanged. Initial-resource
admission rejects zero runtime occurrences and nested exposure snapshots, including
already-validated Python model instances. Resistance and the authored stopping condition
can still end the exposure before it inflicts damage. Immune fatigue targets get
no occurrence. This boundary does not change the existing handling of positive
basic damage that DR absorbs.

`tests/test_composed_cyclic_boundaries.py` exercises all four supported Cyclic
damage kinds at this zero boundary, their later damage and persisted retries,
resistance/termination, and positive hits on either side. The range
oracles in `tests/test_composed_attacks.py` cover default, Increased Range and
Reduced Range immediately below, at and above their approved 1/2D boundaries,
including odd damage totals and DR application. These are bounded actual-resolution
checks for #761/#764, not certification of other attack families or modifiers.

This bounded implementation covers noncontagious Cyclic with ordinary DR and
optional HT resistance, plus the #762 composed-attack join for authored toxic
contagion and independently incubating secondary infections. Symptoms remains
the separate #763 responsibility; [composed attacks](composed-attacks.md) describes
the approved modifier routing. Fractional purchased dice, non-toxic contagious
powers and unrouted modifiers remain unsupported.

Persistent gameplay orchestration does not yet dispatch the composed attack,
exposure or stopping commands. Resource-clock execution and serialized reducer
retries establish engine behavior, not that missing host boundary. No broader
modifier certification or end-to-end compliance claim follows from these tests.
