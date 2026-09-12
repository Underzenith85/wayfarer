# GURPS campaign administration

Issues #501 and #502 add engine-only campaign procedure families. They use the
selected Basic Set Campaigns fourth printing and do not change an engine or API
version while Wayfarer is prerelease.

## Administration (B494-B503)

Reaction commands select a server-authored NPC context and use the existing
reaction scorer. The outcome is an input to later social procedures; it is not an
Influence roll. Dice and modifiers remain GM-private in the resource event.

Knowledge commands select authored fact IDs, audience, and provenance. World
truth, actor knowledge, and GM-only knowledge are separate. A command cannot add
text or choose arbitrary fact IDs.

Awards append an `earned` entry to the existing advancement ledger. The authored
award fixes recipient, amount, and reason; the occurrence command ID and resource
receipt make settlement exact-once and keep earning separate from spending.

Time Use records half-open activity intervals. Concurrent allocations may use at
most 100% of an interval. Each authored activity emits typed study, job, healing,
maintenance, task, or rest credit. Jobs produce study credit at one quarter of
elapsed work time when configured from B499. Settlement advances the existing
shared clock, which orders due schedules by `(due, id)`.

Traps author detection, disarming, trigger, avoidance, reset, and consequence
data separately. Discovery never disarms a trap. Failed discovery reports only
that nothing was found. A triggered, unavoided trap emits a typed consequence for
the existing injury, hazard, effect, alarm, or capture service.

Unsupported activity kinds, trap consequence kinds, triggers, and profiles fail
schema or engine validation instead of falling back to narration.
