# Character transformations

Issue #500 implements the selected Basic Set transformation procedures on
Characters B294-B296. The implementation is engine-only and uses the existing
character compiler, power reviewer, campaign checkpoint, resource state,
advancement ledger, command receipts, and replay store.

## Authored boundary

A campaign must name every available transformation. Each rule pins an exact
target draft, body identity, source reference, point policy, treatment and
recovery timing, permanence or affliction expiry, and all authority routes.
Unknown paths reject. A target is compiled against the campaign's pinned catalog
and receives the ordinary GM power approval; a transformation cannot rename the
character or replace their history.

Every old and new purchase has an explicit `mind`, `body`, or `neither` route.
Mind transfer keeps IQ and learned skills with the mind, while ST, DX, and HT
come from the body. The five non-build domains - inventory, credentials,
relationships, knowledge, and control - must also be declared exactly once.
This makes campaign-specific metaphysics data rather than hidden engine policy.

## Persistent lifecycle

The transformation ledger records proposal, exact target revision, proposer,
approver, treatment deadline, interruption, active body, recovery deadline,
expiry or cure, source attribution, and both source and target approvals.
Retries return the committed record and stale campaign or build revisions fail.
Owner-scoped projections hide another player's proposed build and routing data.

Treatment can be interrupted before its deadline without changing the build.
Completion rebases HP and FP maxima while preserving the existing deficit, so a
new body is not an implicit heal. Temporary or curable afflictions restore the
recorded prior build and authority state. Permanent changes reject expiry.

Point-value changes append `transformation` entries to the existing advancement
ledger. `adjust` records value without creating spendable points; `charge`
requires available discretionary points; `debt` records a negative balance for
future awards. Optional item payment is consumed through the existing inventory
engine.

Death remains authoritative. Only a selected `death-transformation` whose rule
explicitly requires death may cross that boundary. It clears the death marker
and the injury record's dead flag, but preserves injury deficits and conditions;
it never rebuilds a dead character as a healthy replacement.

## Explicit exclusions

The following adjacent variants remain unavailable unless separately authored:

- autonomous copies or multiple simultaneously player-controlled minds;
- unselected resurrection, reincarnation, undead, or pure-thought forms;
- inferred trait classification or an incomplete mind/body mapping;
- implicit cures, expiry, cash conversion, point refunds, or healing.

Independent expected-result fixtures are in `tests/test_transformations.py`.
