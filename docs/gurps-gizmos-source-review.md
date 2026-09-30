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
