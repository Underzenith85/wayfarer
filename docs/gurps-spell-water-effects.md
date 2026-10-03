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

The merged baseline supplies actual consequences for all four named spells.
The acceptance evidence is independent of catalog verification flags:

| Spell | Construction and source | Actual successful consequence | Ordinary failed consequence and lifecycle |
| --- | --- | --- | --- |
| Seek Water | IQ/Hard, B253; approved budget | Nearest significant source; caster receives direction, distance and nature and can exclude the remembered source on a later cast | No finding or knowledge; ordinary failure payment; default one-second cast |
| Purify Water | IQ/Hard, B253; Seek Water prerequisite | Ordinary uninterrupted ring/finger transfer removes impurities; source loses selected gallons and receiver gains pure gallons | No material transfer; ordinary failure payment; 1 energy/gallon and declared 5–10 seconds/gallon |
| Create Water | IQ/Hard, B253; Purify Water prerequisite | Receiving container gains permanent pure water; admitted one-gallon mist separately records material and extinguishes supported enclosed fires | No material or fire changes; ordinary failure payment; 2 energy/gallon and default one-second cast |
| Destroy Water | IQ/Hard, B253; Create Water prerequisite | Selected isolated square-area portions disappear, including liquid, ice and steam, within the supported two-yard depth | No material changes; ordinary failure payment; base 3 per radius and default one-second cast |

Base costs and times above precede the shared B236–237 skill adjustments.
Successful effects end their casting record immediately; material and findings
persist without maintenance. Unsupported requests are rejected rather than
reported as successful effects. The nonliving-world-object admission prevents
creation inside a foe and destruction as a dehydration attack.

`test_water_construction.py` proves all four legal source-linked purchases and
illegal missing prerequisites or budget. `test_water_cast_persistence.py` runs
each approved spell with independently asserted costs, times and successful or
failed resulting material in SQLite and PostgreSQL. It checks restart, exact
retry, stale revisions and channel/gallons/actor substitution rollback before
dice. `test_water_seeded_replay.py` reexecutes all four spells from recorded seeds,
compares folded and reexecuted commands and final campaign state, and checks
material after later time advancement. `test_water_effects.py` supplies independent
nearest-source, purity, area, form, depth, refill and living-body restriction
oracles. `test_water_persistence.py` rejects player-authored material facts.

The companion execution documents and their host tests extend that baseline:
[remembered Seek findings](gurps-water-discovery.md),
[receiver recontamination](gurps-water-purification-mixtures.md),
[complete heterogeneous-source purification](gurps-water-complete-source.md),
and [one-gallon mist and canonical fire extinction](gurps-water-mist.md).
These cover current authority and private projection, changed current source or
scene refusal, restart/retry and deterministic reexecution in both stores.
Historical omitted-default commitment bytes and the five public spell schemas
remain pinned by `test_water_purification_legacy.py` and `test_water_schema.py`.

This reconciles the four named-spell written baseline for issue #804. It does not
claim every physical variant or the entire water college is implemented. Issue
closure remains contingent on current verification and explicit follow-up
ownership of the unsupported contracts below.

Remaining unverified contracts are owned by open follow-up
[#981: Complete remaining Water material and geometry carriers](https://github.com/Underzenith85/wayfarer/issues/981):

- Exact fractional liquid and purity accounting, partial heterogeneous-source
  parcel composition, and all affected material consumers. Mass fractions are
  not fluid-volume evidence; fractional FP rounding is not established here.
- Accelerated large-container/ring purification with actual current vessel,
  ring and continuous-flow/time facts. B253 supplies no faster numeric rate or
  large-ring threshold; neither is invented by this baseline.
- Physical creation and destruction geometry: immediately falling airborne
  globes, collection/flow, deep partial columns and surrounding-water refill.
  These require actual geometry/material carriers and resulting-state consumers.
- Broader mist geometry and live fire carriers, including hex/combat placement,
  multi-cell object extents and moving clothing fires. The current single-cell
  scene refuses ambiguous or unsupported carriers; it does not certify them.
- Water item casting through an authorized item-specific host. No inventory
  status substitutes for the actual casting and material consequences.
