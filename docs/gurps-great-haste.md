# Great Haste and actor-relative maneuver opportunities

The private implementation is reviewed against Basic Set Characters B251 and B38.
Private rules files and printed source prose are excluded. Issue #797 includes
other movement spells and is not closed by a bounded Great Haste slice.

Great Haste is Regular and IQ/Very Hard. It requires Magery 1, IQ 12 and Haste;
listed casting energy is 5, casting time three seconds and duration ten seconds.
It grants one level of Altered Time Rate and cannot be maintained. Positive
subject Size Modifier scales Regular casting energy. At the effect's end a
non-caster subject loses 5 FP; a caster who targeted themselves is exempt from
this additional loss. End fatigue is an involuntary consequence, not a new
voluntary expenditure that can be declined because the subject has too few FP.

The opted-in movement learning catalog checks IQ 12 with the existing purchased-
definition metadata: attribute purchases record their absolute score. This reads
raw purchased IQ before Magery adds to spell learning. It does not expand the
public prerequisite enum or the Workshop contract; the default movement package
and its historical pin remain unchanged.

B38 gives an additional maneuver during the same actor's existing combat turn.
Multiple Move maneuvers can move twice, and multiple Concentrate maneuvers can
accumulate subjective casting seconds. Initiative does not become faster. A
completed initiative cycle still settles one shared real second.

## Canonical maneuver budget

New accepted combat inputs capture a private feature generation. Historical
inputs without that generation retain their original single-maneuver timing,
even if their approved actor had purchased Altered Time Rate. Exact retries use
the recorded generation; deterministic replay receives the same recorded input.
The public combat command vocabulary and Encounter JSON schema are unchanged.

The private, omitted-when-absent Encounter budget freezes the acting actor,
round, initiative index and remaining maneuver opportunities. It is captured
before the first ordinary maneuver, persists while attack defense or damage is
pending, and is spent when the original maneuver finishes. It is distinct from
All-Out Attack's second attack within one maneuver. An interrupted actor's Wait
reaction resumes the original budget without consuming another opportunity.
Defense-use counts, retreats and reaction state are retained between maneuvers;
these refresh when the next real turn starts. Physiological turn-start and
turn-end processing runs at the first and last opportunities respectively.

## Spell-host boundary

The initial private spell host casts outside an active encounter containing
caster or subject. Its paid effect can then provide real extra maneuvers after
StartEncounter. The host derives skill, prerequisites and payment from approved
pinned builds, and uses trusted immutable actor/location/range/mana channels.
Cancellation of an accepted cast remains available without reauthoring target
facts. End fatigue uses an immutable activation witness and an idempotent end
record, so ordinary expiry, cancellation, restart and exact retries can settle
one canonical fatigue consequence per successful cast. A concurrent Great Haste
cast on an already casting or affected subject is rejected before dice or payment:
B251 does not resolve this bounded host's overlapping-recast timing. Purchased
Altered Time Rate remains a separate native capability.

Cancellation or expiry removes only the spell-derived unspent opportunity.
Native Altered Time Rate opportunities remain. A maneuver already launched with
a pending response keeps its settlement lease and finishes normally; cancellation
does not create another maneuver. When no pending response or native opportunity
remains, the canonical combat settlement ends the real turn once. The outer
command retains one state/resource revision even when end fatigue also settles.
Clock checkpoints visit the exact ten-second expiry before advancing further.

Casting Great Haste during an active encounter needs a further shared spell
concentration adapter. Existing spell completion requires real-clock casting
seconds; merely allowing a second Concentrate maneuver does not implement its
spell timing. Mid-turn activation and external clock advancement during pending
attack settlement also need an explicit opportunity policy before widening the
host. These boundaries remain open acceptance work rather than inferred rules.
