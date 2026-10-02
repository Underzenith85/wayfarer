# Water material effects, issue 804

Reviewed Characters third printing, printed B253 (physical PDF page 255),
with ordinary spell learning/casting on B235–241. The four owned spell learning
rows use IQ/Hard and the printed prerequisite chain Seek Water → Purify Water →
Create Water → Destroy Water. Other water college rows remain unreviewed learning.

Private water records represent placed nonliving world objects, actual whole
gallons and purity, receiving capacity and authored source significance. They
preserve world-object custody; purity and volume are not a success-string proxy.
Seek Water chooses the nearest significant current source, applies B241 distance
and missing forked-stick −3, records direction/distance/nature for its caster,
and admits exclusions only for sources that caster knows before starting.

Purify Water transfers a continuous ordinary stream through a ring/fingers to a
separate receiving container, costs 1 per gallon, and uses the declared printed
5–10 seconds per gallon. Pure material persists until later contamination.
Create Water adds permanent pure water to a receiving container at 2 per gallon.
Destroy Water removes the actual selected isolated one-yard-column portions in
its bounded square area, base cost 3 per radius, at most 2 yards deep, including
ice or steam. Living creatures cannot be authored as water objects.

`WaterService` declares water/channel facts under current seated director
permission, then routes casting through the canonical approved-build spell
lifecycle and campaign revision/receipt pipeline. Shared runtime joins retain the printed Special/Information kinds privately,
while the public SpellSpec keeps its original closed kind field. A separate
water-cast-plan ledger captures actor, channel and material plan at start;
substitution is rejected before dice or material changes. Public SpellEffect
and historical spell events gain no fields. Five public model schemas match
verified main 33271dca byte for byte. Successful water effects end their casting
record immediately; the material/discovery record survives time advancement.

Sixty-one focused tests cover independent material/construction expectations, real
approved casting for all four spells in both SQLite and PostgreSQL, ordinary
failure payment without material changes, director authority, exact retry,
restart, stale revisions and event replay, and actual channel/gallons/actor
substitution rollback without dice. Eight seeded command re-execution cases
cover all four spells in both stores through the registered private replay
family, with folded and re-executed events and the entire final campaign equal.
Actual material/discovery, persistence after time advancement and exact retry
are checked.

Remaining explicit boundaries: fractional gallon energy/flow; source mixtures
whose flow composition is unspecified; contaminated receiving-vessel mixing;
large-container/ring accelerated purification; created airborne globes and
mist/fire consequences; deep-water partial columns and surrounding refill;
hex/combat placement; item casting. These are not implemented by inventory
status. Full issue 804 acceptance still requires the remaining variants.
