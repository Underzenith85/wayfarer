# Rescue loyalty (B519)

This bounded correction is a prerequisite of open #868. It does not complete
reaction Luck or certify the full loyalty procedure family.

## Source and behavior

Checked against **GURPS Basic Set: Campaigns**, Fourth Edition, fourth printing,
B518-519, with B494-495 and B560-561 for reaction scoring. The supplied private
PDF has SHA-256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
No source PDF or copied source text is included here.

The GM authors a rescue circumstance only when the PCs risk their lives or the
mission for the hireling. B519 calls for a reaction with a rescue adjustment of
at least +3. B494 adds modifiers to 3d and favors higher results; it has no
success target. Good begins at 13. On Good or better, the contract's loyalty
becomes the greater of its previous rating and the modified reaction total.
A lower reaction leaves it unchanged, even when its total exceeds old loyalty.

Rescue still rolls when loyalty is 20 or higher. That automatic-pass rule belongs
to ordinary loyalty checks, not this reaction. There is no cap of 20 on the
reaction or resulting hireling loyalty. The existing pay bonus is an ordinary
loyalty-check adjustment; this rescue does not add it to the reaction or save it
as permanent base loyalty.

## Existing authored records

`LoyaltyCircumstance(kind="rescue")` uses its existing `modifiers` tuple for the
GM-authored rescue situational adjustment. The existing empty tuple receives the
printed +3 by default. Explicit components must total at least +3,
have distinct source IDs, and carry integer values, reasons and versioned source
provenance. They must be situational modifiers. There is no implicit additional
+3: a supplied +3 is counted once. These fields describe the qualifying rescue,
not a fresh inference from a combat or injury receipt.

The generic `loyalty_change_on_success` and `loyalty_change_on_failure` must both
be zero for a new rescue command. Serious injury or death during the rescue may
justify an extra permanent bonus at the GM's discretion under B519; the ordinary
reducer does not infer it. The private reaction continuation can bind that
separate explicit GM decision through `RescuePermanentBonus`; see
[campaign reaction continuations](campaign-reaction-continuations.md). General reaction
standing or recognition is not inferred from the initial recruitment context.

The new branch rejects malformed rescue inputs before rolling or changing
economics/resources. It checks the active contract, circumstance binding, and
authored employer/hireling actor identities. The ordinary danger, temptation,
service and competence paths retain their existing success-roll and additive
behavior. This preservation is not a new claim of source-wide verification of
those paths.

## Consequence and replay boundary

`CampaignProcedureEngine.apply` reaches the canonical economics reducer. A
rescue updates `HirelingContract.loyalty` and appends its `LoyaltyCheck` through
that real path. In the rescue record, `passed` means Good-or-better gratitude;
the public outcome is `grateful` or `loyalty-unchanged`. No dice, numeric loyalty,
modifier trace or private motive is added to the resource event.

Existing receipt lookup precedes the new rescue validation. An exact retry,
including a restored historical additive rescue receipt, returns its previously
committed outcome/state without a new roll or reinterpretation. Changed payloads
using an existing command ID still conflict. The reducer consumes exactly three
faces from its caller's RNG for a new valid rescue.

This campaign reducer has no current orchestration command recorder or
seed-only replay handler. The tests verify deterministic caller-supplied seeds,
actual returned campaign state, receipt retries and checkpoint restoration;
they do not claim database persistence, command-history reexecution, a pending
reaction host, or Luck integration. Those remain part of #868's planned typed
campaign continuation on the existing transaction/authority/clock host.

## Independent numeric coverage

`tests/test_rescue_loyalty.py` checks literal source boundaries, including:

- old 14, raw 15, +3 becomes 18
- old 14, raw 9, +3 remains 14; old 6 also remains 6 at total 12
- old 12, raw 10, +3 becomes 13; old 16 stays 16 at the same qualifying total
- old 20, raw 18, +3 becomes 21; old 25 stays 25
- old 20 still consumes a reaction and does not qualify at raw 3 plus 3
- a larger authored bonus works, and higher pay does not alter the reaction

It also covers an actual hire-then-rescue sequence, ordinary-check regressions,
malformed contexts before entropy/effects, exact retries and a literal historical
receipt. No public API/schema version or historical fixture is changed.
