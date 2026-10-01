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

The private persisted `CyclicService` accepts current seated and deployment-trusted
GM observations through the existing command pipeline. A stopping observation
names its occurrence, subject, current location and factual reason; the named
condition comes from the immutable source binding. It cannot supply a new condition,
HT, skill target, successful roll or damage. Source policies with a mechanical
procedure require a recorded start, actual elapsed ordinary campaign time, GM
observation of uninterrupted performance, a currently capable approved performer,
consumption of the approved resource, and the approved DX/IQ/Physician check when
required. A failed check consumes the attempt and resource without stopping the
attack. Observation, stop and release of that occurrence's recovery debt commit
atomically; old exact retries return the original private receipt after current
authority checks. General observations and continuous performance are explicitly
GM adjudication, not an automatically detected scene fact.

New host occurrences carry private immutable source policy events. Their clock
requires an approved target context resolver, recomputed at every chronological
deadline against the intermediate resource state. Purchased dice, penetration and
source semantics stay pinned to exposure. Current natural, worn and active torso
DR, current vulnerability and canonical body physiology supply repeat damage;
current permanently approved HT supplies resistance. B421 exempts those defensive
rolls from temporary attribute penalties, including Symptoms arising between ticks.
A bare resource advance cannot reach a bound deadline without current context and
RNG settlement. Legacy occurrences without bindings retain their recorded snapshot
semantics. Source removal after a valid hit does not erase an existing occurrence.

Characters third printing B61-62 excludes machines from ordinary fatigue and
toxic Innate Attack damage. New bound cycles use the current canonical machine
fact at each deadline, skip damage and resistance draws for an immune body, and
retain prior injury/debt while the remaining cycles elapse. Becoming living again
restores ordinary future consequences. Pending ordinary toxic contacts likewise
finish without a roll, infection or debt while the subject is a machine. Contact
observed on an already-immune machine finishes immediately and does not contribute
to a later living body's exposure modifiers. Neither case cures an independently
established infection or grants permanent natural
immunity. Such skipped contacts do not consume B443's first actual disease roll.
Legacy unbound occurrences and contacts preserve their recorded behavior. Special
machine-affecting toxic variants are not inferred or added by these ordinary rules.

`tests/test_cyclic_host.py`, `tests/test_cyclic_current_context.py` and
`tests/test_cyclic_host_replay.py` cover the persisted observation service, real
ordinary time before stopping, independent services racing/retrying, current
membership, restart and seed-only reexecution, and the resource-clock context
rules. Their fixtures restore an already-resolved canonical attack checkpoint;
actual composed delivery is additionally tested in
`tests/test_composed_cyclic_host_integration.py`. `tests/test_cyclic_play_clock.py`
executes the shared PlayState clock, including automatic body reversion between
deadlines and actual held-item custody. See [clock integration](cyclic-play-clock.md)
for the implemented caller paths and the explicit remaining resource-only campaign
procedure boundary. These tests do not promote broader modifier certification.


Persisted modifier parameters retain their canonical JSON and approval/build
digests. Runtime validation now treats declared defaults (null, false, empty
collections, and the default reload selection) as inert rather than newly authored
parameters after JSON expansion. Meaningful unsupported values still reject, and
required values remain required; explicit HT+0 remains meaningful because its
default is null. `tests/test_modifier_persistence.py` verifies this prerequisite
without changing public schemas or claiming additional modifier consumers.


Private occurrence bindings can retain an actual resolved hit location, including
an Innate Attack critical self-hit. Repeats use that location's current natural and
worn protection and canonical location injury, with current hand bindings for item
loss. Disease transmission starts a new internal illness and does not inherit the
original delivery limb. General current check modifiers such as Fright aftermath,
and B55 Fitness bonuses, remain separate from the unchanged resistance attribute.
The low-level resolver cannot relocate an exposure if its exact body part is later
severed or the actor changes to anatomy without that location. The private
`AdjudicateCyclicLocationLoss` command lets a seated, deployment-trusted GM retire
that occurrence under an explicitly named campaign policy. This is recorded as
GM campaign adjudication, not a printed automatic stopping rule or completion of
the original stopping procedure. It requires the immutable binding to name a limb
and the current canonical body to show that exact limb missing (including a hand
under a severed arm), or to declare creature/swarm anatomy incompatible with its
human location. Undeclared anatomy, injury or crippling of a still-present limb,
loss of another limb, disease, torso and unpinned delivery do not qualify.

The retirement preserves the original source, location, damage history and debt,
marks only that occurrence and its recovery restriction inactive, and records the
policy, GM, factual reason, current anatomy and matching severance evidence. It
does not heal HP, move the exposure to the torso, or assert success of a source
procedure. Already-due occurrences can be retired independently so multiple effects
on the same lost limb do not strand a valid checkpoint. Retirement advances no
time and leaves other due occurrences and all ordinary clock barriers unchanged.
`tests/test_cyclic_location_adjudication.py` uses restored canonical injury/body
checkpoints and actual service transactions and Wait commands to verify both
stores, exact and competing retries, current authority, refusal cases, recovery
eligibility, restart and seed reexecution. The test does not claim an automatic
source rule for the interaction or exercise delivery/body-change admission itself.

Selected Campaigns fourth printing B379 also requires corrosion to reduce DR for
later attacks, exhausting armor before natural DR, with separate recovery of
living natural DR. The existing canonical injury reducer applies corrosion injury
but does not yet record that protection loss. Re-querying current protection does
not supply a missing corrosion-damage ledger. Initial corrosive delivery remains
an implementation obligation of #764 and its repeated consequence of #761; the
HP-only corrosion cases are not full corrosion compliance or closure evidence.


The source ledger remains partial with completion owner #764 for ordinary corrosion
DR destruction and the remaining general delivery envelope. The source-defined
selected noncontagious and transmission behaviors have separate bounded acceptance
under #761/#762; none of these flags or tests certify every damage type or modifier.
