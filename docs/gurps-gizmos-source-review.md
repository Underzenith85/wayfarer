# Ordinary Gizmos source review (#766)

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

## Explicit remaining variant

The B58 “Gadgeteers and Gizmos” extension is **unimplemented and unverified**,
tracked by follow-up #853 under #742.
It includes small inventions and building equipment on the spot with actual
materials, required skills, a secret skill roll at -2 or worse, use consumption
on failure, and critical-failure backfire. This reducer rejects authored
Gadgeteer inventions rather than claiming those procedures work. Ordinary
Gizmos evidence does not certify that extension, the whole technology family,
or the Basic Set profile. The implementation/source-review parent statuses
are not promoted to verified by this change.

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

Secret check traces live in a separate GM-only snapshot. The host must commit
that snapshot and resources together; only the ordinary item/use result belongs
in player projection. Actual device condition, actor HP, remaining materials,
use expenditure, restart/replay, authority and pre-entropy rejection are tested.
Shared Gizmo event IDs are now bounded hashes, with legacy event lookup retained,
so a valid 200-character command cannot fail solely from a prefix after rolling.
No source certification or broader Basic Set/end-to-end gate is promoted.
