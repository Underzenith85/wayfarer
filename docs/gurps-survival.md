# GURPS survival procedures (#516)

Issue #516 completes the Basic Set fatigue and survival-cost family for the
selected Campaigns fourth printing. The implementation is engine-only: scenario
adapters bind physiology, compiled skills, terrain, party membership, and supply
items before calling these reducers.

## Fatigue and deprivation

The shared fatigue reducer remains the only path for FP loss. It preserves the
low-FP ST/Move/Dodge boundary, the Will gate at nonpositive FP, negative-maximum
unconsciousness, and HP spillover. Battle, hiking, running/swimming adapters, and
heavy exertion use the common cost helpers described in
[fatigue and medical recovery](gurps-recovery.md).

`SurvivalStatus` adds persistent deadlines for one meal and one water assessment
per eight-hour interval. An authoritative settlement consumes individually
identified inventory quantities first, then records starvation or dehydration
fatigue. Water requirements are two, three, or five quart units per day according
to the authored climate. If the daily total remains below one quart, the third
interval adds the separate daily FP and HP consequences. Every settlement has a
command receipt and advances its deadline before returning, so retry, JSON reload,
and replay cannot consume supplies or apply a loss twice. These timings and
boundaries derive from Campaigns B426. The common FP threshold and recovery
summary in Characters B328 provides the cross-volume fatigue boundary.

## Sleep

The survival clock records the actor's waking day and required sleep period.
Missed sleep first costs FP at the waking-day deadline and again by quarter-day.
At half the actor's FP in sleep debt, the clock schedules persistent Will checks;
the interval tightens at the lower FP boundary. A successful check records the
temporary drowsy DX/IQ modifier, while failure records forced sleep and blocks
voluntary activity.

Sleep is an interruptible timed activity. A full sleep period restores one unit
of sleep fatigue, with further uninterrupted hours restoring further units;
ordinary fatigue also recovers at the quiet-rest rate. Short sleep moves the next
waking deadline earlier using the recorded missing hours. The Doesn't Sleep
physiology rejects the sleep procedure. Pending sleep, medical rest, dedicated
foraging, and incompatible resource activity cannot receive simultaneous time
credit. These rules are sourced to Campaigns B427.

## Foraging

Foraging snapshots trusted Survival/Naturalist and missile-or-Fishing skills,
terrain modifier, party HT values, output definition, and supply owner at its
start. A travel day grants one plant attempt and one hunting/fishing attempt; a
dedicated day grants five of each. Missile hunting applies its authored penalty,
plant successes produce one meal, and hunting/fishing successes produce two.

Resolution draws only from the supplied recorded entropy and creates one stable
ration stack. Retrying the finish command returns the stored result without
rerolling or minting another stack. Plant-roll poison boundaries record each
affected actor's HT check and injury; the whole-party case uses the party snapshot
rather than a caller-selected target list. Unsupported durations, profiles, water
bands, and unbound supply owners reject explicitly. Source: Campaigns B427.

## Evidence

`tests/test_survival.py` independently checks FP/HP boundaries, daily and
eight-hour settlement, supply conservation, sleep interruption and recovery,
drowsiness, travel and dedicated foraging, poison injury, JSON restart, and
exact-once retry. Existing fatigue, medical, resource, architecture, and contract
tests cover the shared reducers and persistence boundaries.
