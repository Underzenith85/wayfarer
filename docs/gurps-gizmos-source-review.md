# Gizmos source review (#766, #853)

Selected source: Basic Set: Characters, Fourth Edition, third printing, printed
B57 (Gizmos). Checked against the supplied source text before implementation.

- Purchase costs 5 points per level, with a maximum of three ordinary Gizmos.
- Each purchased level permits one revelation per game session. Passage of game
  time, resting, changing scenes, or transfer of a revealed item does not reset it.
- One small item must fit in an ordinary coat pocket and be equipment the actor
  could have carried. No numerical size or price threshold is invented.
- The trusted GM selects one source category: an owned but undeclared item;
  probably owned, minor/ignorable equipment consistent with character concept;
  or an inexpensive device widely available at the actor's TL.
- Before revelation the item is absent from active inventory. It cannot be found
  by searching that inventory, damaged, stolen, or lost there. Revelation adds
  the approved actual instance to the existing inventory ledger; its weight,
  ownership, quantity, condition, charges, and subsequent custody use the normal
  ResourceEngine validation and procedures. An owned instance keeps its ID.
- A GM-approved session command establishes the reset boundary. Historical IDs
  cannot be reopened. Exact command retries return the original outcome and
  create no second item; changed payloads, stale revisions, and reused
  eligibility/instance IDs reject before mutation. Authority is required even
  on retries. Invalid eligibility and failed inventory validation consume no use.

Executable evidence: `tests/test_gizmos.py` inspects actual inventory, carried
weight, transfer custody, source cost/level bounds, all three categories, session
limits/reset, GM authority, replay/staleness, and atomic rejection. Runtime:
`src/wayfarer/engine/simulation/traits/gizmos.py`.

## Gadgeteer extension (B58, #853)

The selected Characters third printing permits an approved Gadgeteer to reveal
one of their small inventions or build it on the spot with appropriate actual
materials and learned required skills. `gadgeteer_gizmos.craft_gizmo` joins the
approved Gadgeteer/Gizmos purchases to the existing real session-use history.
An existing invention preserves its identity, custody and condition without a
crafting roll. Crafting consumes authored material instances in actor custody,
then rolls the approved relevant skill secretly at -2 or worse. Failure still
spends the Gizmo and creates an actually disabled device. Critical failure also
applies an explicitly GM-authored backfire through canonical injury; B58 supplies
no universal backfire damage formula, so the amount is required source context.

Secret checks now live in canonical resource events, whose existing event-stream
projection is GM-only. `GadgeteerGizmoService` uses the shared `CommandPlan`,
`PlayService.commit`, and campaign compare-and-set transaction. The inventory,
material consumption receipts, session use, injury, secret roll (including the
explicit GM penalty), and outer command receipt therefore commit or roll back
together. There is no detached snapshot for a caller to forget. Successful,
failed and critical attempts each advance one outer revision. Player inventory
and HP projections contain the actual consequences but no secret roll trace.

Material consumption uses the existing ResourceEngine instead of editing counts
around its guards. Carried container contents are available; foreign, grounded,
missing, repair-reserved or over-reserved ammunition inputs reject before rolling.
The recipe can free carrying capacity before the finished item enters inventory.
Required purchases must be actual learned skills, not attribute purchases.

A failed device cannot function through general item use, general/electronic
activation, a fuel input, container storage, or ammunition loading/firing. Its
ordinary custody and transfer remain legal; the existing repair procedure can
restore it, and the existing salvage procedure remains available under its own
rules. These checks do not prevent handling a broken device as an object.

Executable evidence: `tests/test_gadgeteer_gizmos.py` and
`tests/test_gadgeteer_gizmos_persistence.py` inspect actual material quantities,
device condition, custody, repair, HP, shared session limits, explicit -2/-4
penalties, learned skills, GM event audiences and player projections. SQLite and
PostgreSQL cases cover concurrent exact retries, restart/fold, one revision per
attempt, and rollback after the candidate checkpoint has been produced. A revoked
GM seat is rechecked before retry disclosure and under the transaction lock.
The original `tests/test_gizmos.py` remains the ordinary B57 baseline.
Shared Gizmo event IDs are now bounded hashes, with legacy event lookup retained,
so a valid 200-character command cannot fail solely from a prefix after rolling.
No source certification or broader Basic Set/end-to-end gate is promoted.

### Acceptance boundary

The bounded #853 procedure is implemented for GM-approved, catalog-backed small
inventions with actual inventory/material identities, an approved Gadgeteer and
Gizmos build, and learned skill bindings. Crafting requires a durability profile
for a nonsentient object so failure can persist a truly disabled item; critical backfire requires an
explicitly approved injury amount and canonical actor health. Unknown device
mechanics, automatic invention design, and other unmodeled GM consequences are
not inferred from prose. This review does not certify the broader technology
family or promote the Basic Set profile. No public API/UI or new rule source is
introduced.

On-the-spot sentient-machine construction remains unsupported: its existing actor
HP authority cannot carry the ordinary item failure condition. Such recipes now
reject before any roll or material/use expenditure, regardless of the potential
roll outcome. Revealing an existing valid sentient invention preserves its
identity and actor-health binding without crafting it.
