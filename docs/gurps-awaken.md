# Awaken: supported engine subset

Characters third printing B248 specifies Hard learning, Lend Vitality prerequisite,
Area class, base cost 1 and default one-second casting. The approved adapter derives
HT for every participant inside the authored square or hex area, never from caller
claims. Radius scales casting cost under B239; high skill reductions remain shared.

Successful casts clear canonical physical/electrical/surprise stun, mental backfire
stun and active stun afflictions. Sleeping/unconscious subjects receive HT plus the
caster's margin, with −3 for recorded injury unconsciousness and −6 when drugged.
A failed awakening roll preserves unconsciousness while still removing stunning.
FP≤0 subjects receive no effect. Dead subjects are unchanged; waking does not heal
a mortal wound or cure a continuing drug condition.

The represented consciousness sources are injury/fatigue, active sleep or
unconsciousness afflictions, legacy actor-only unconsciousness, survival sleep,
sleeping rest, toxin unconsciousness/overdose and alcohol stupor/coma. Waking facts
update legacy actor condition mirrors in the same transition, including automatic
completion during a Concentrate maneuver. Unrelated restraint remains unchanged.
Sleep is interrupted without granting an uncompleted night's recovery, resetting
sleep debt, or erasing a due settlement. Ordinary quiet rest remains uninterrupted.

Drug wake overrides are private resource-event records keyed to each exposure or
intoxication. Existing authored-state and API schemas are unchanged.
Exposure damage, cycles, symptoms and deadlines remain; another failed
incapacitating toxin cycle can cause unconsciousness again, while a resisted cycle
does not undo a successful awakening. Alcohol levels and sober/hangover deadlines
are retained, including the medical-care requirement for coma recovery. Continuing
DX/IQ checks retain B428/B440 intoxication penalties. Awaken does not grant immunity
to paralysis or other unrelated drug consequences. Wider alcohol self-control,
social and medical-condition coverage is not established by these tests.

A successfully awakened subject below one third of basic FP owes exactly 1 FP at
the hour deadline. The shared clock rejects skipping any unpaid deadline, accrues
earned rest through the deadline, then applies the canonical magical-fatigue cost.
History and receipts preserve expiry and retry behavior without a second charge.
Alertness suppresses fatigue drowsiness and its sleep checks during the interval;
fatigue debt itself is unchanged, and ordinary drowsiness can resume afterward.

B237 overlapping effects are represented as a single alert state through the
latest still-active expiry, with no additive alertness bonus. The B248 deferred
1 FP consequence belongs to each successful cast; recasting neither cancels an
earlier debt nor moves its deadline. This keeps simultaneous effects non-stacking
without treating a suppressed interval as a refund of its casting consequence.

**Remaining boundaries for #774:** HP-only unconsciousness without a recorded
cause still rejects before casting or randomness, rather than guessing a source
penalty. Ordinary world-area casting has no authoritative per-actor placements:
world location IDs alone cannot establish area membership. Noncombat/world-area
Awaken therefore remains unsupported; the sleep cases above require the existing
encounter placement channel. The bounded implementation does not promote overall
source certification or provide new API/UI/voice routes.

Evidence: `tests/test_awaken_spell_effects.py` and
`tests/test_awaken_consciousness.py` inspect actual pools, drug records, condition
mirrors, sleep tasks, eligibility and continuing checks. They cover failed waking
and casting, ineligible/dead subjects, invalid-before-RNG cases, costs, overlap,
clock expiry, approved-build execution, authority, stale revisions, SQLite and
PostgreSQL concurrent retries, private roll traces and checkpoint reload/replay.
The shared Size checkpoint and Lend Vitality clock operations are unchanged.
