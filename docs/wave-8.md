# Wave 8 implementation checkpoint

This branch is **not the completed Wave 8**. Do not merge it or start Wave 9
until every issue below meets its acceptance criteria and all applicable checks
have completed successfully on the exact PR head.

Baseline: main `ff29d5c994fcff8e18ea18abeb5b425f8423f498`, whose tree
`f2207ce4f8e51b0c15a1a102fc8abf259f5b478b` matched the local baseline exactly.
Roadmap issue #1 remains authoritative.

## Delivery checklist

- [x] #15: bounded, persisted adjudication and approval workflow for the supported
  social-check subset, including feature-level SQLite/PostgreSQL contracts.
- [ ] #16: encounter lifecycle, action economy, tactical position, maneuvers,
  reactions, posture/facing/readiness, and persisted defense-choice pauses.
- [ ] #18: advancement ledger, compiler-backed purchases/refunds, migration
  previews/diffs, approved atomic ruleset migrations, and recovery/replay.
- [ ] #19: campaign membership, every-read/write authorization, typed transport,
  resumable perspective-safe streams, request/rate limits, health/correlation.
- [ ] #34: versioned scene/exit/obstacle/discovery contracts, travel/investigation,
  automatic observations, once-only triggers, perspective journals and revisits.

Resume this branch and its existing PR; do not create a second Wave 8 PR. Add
issue-closing references for the remaining issues only when their implementation
is complete. Keep all existing quality gates and PostgreSQL CI services intact.

## Bounded adjudication (#15)

`ActionRules.adjudication` is an optional, server-authored `RulingPolicy`. It
defines named alternatives, affected mechanics, check IDs, modifier bounds,
automatic-approval bounds, player approval permissions and game-time lifetime.
GM identity comes from the existing trusted `PowerReviewer.gm_ids` configuration.
Request JSON cannot supply alternatives, modifiers, outcomes, authority or rolls.

The currently implemented alternative explicitly **reframes an ambiguous
deception/intimidation proposal as a diplomatic appeal** against its existing
scenario social check. Its label is presented for approval; it is not silently
treated as successful deception or intimidation. This does not add published
GURPS rules, a universal arbitrary-ruling language or unsupported mechanics.
More resolution kinds must be added explicitly alongside their engine support.

`AdjudicationService(PlayService(...))` exposes these typed operations:

1. `submit(cid, RequestRuling(...), authenticated_actor_id=...)` records a pending
   ruling only after both the original proposal and supported alternatives pass
   their ordinary capability/target/skill/equipment gates. Its nested action must
   match the authenticated actor and expected revision. The transaction stores
   the original action, authored alternatives and exact configuration/policy pins.
2. `submit(cid, DecideRuling(...), authenticated_actor_id=...)` records approval or
   rejection by a configured GM, or by the affected player when policy permits.
   Decisions may select only an existing alternative ID. The reason is opaque
   audit text, never an executable instruction. Approval does not roll dice,
   spend resources, advance time or reveal facts.
3. `submit(cid, ExecuteRuling(...), authenticated_actor_id=...)` requires the
   affected actor, canonical approval and the exact approved state revision. It
   rebuilds the approved action server-side and revalidates all ordinary action
   gates. Only then does the existing action engine roll, spend resources, advance
   time and reveal the configured facts. The trace retains both the scenario
   modifier and ruling modifier with their separate provenance.

The trusted `evaluate(cid, ruling_id, command_id=..., expected_revision=...)`
hook selects the **first server-authored alternative** within the policy's
automatic bounds. It records `system:adjudication` as the deciding identity.
It does not accept an LLM decision, and `evaluate_ruling` is deliberately absent
from the public command-input union. Do not expose this hook as a model tool.
When automatic approval is disabled or no option is within bounds, evaluation
fails without any write and leaves the ruling for human review.

The policy's modifier bounds govern the additional adjustment, not an arbitrary
replacement target; the combined scenario/ruling adjustment also remains within
the engine's -20 through +20 bound. No alternative can invent a skill, event,
state mutation, cost, dice value or secret.

### Persistence and expiry

The phases use separate short `commit_turn` transactions. No transaction stays
open while awaiting approval or model input. The immutable event log records
each transition and its resulting checkpoint, including actual execution dice.
Exact retries return the original phase result even after subsequent play.
Changed-payload retries and competing commands cannot reuse a revision.

Consent is conservative: any unrelated committed play change expires pending
or approved rulings. A decision advances its own ruling's valid revision once;
execution consumes that approval once. Ordinary actions and character approvals
materialize expired status in their next checkpoint. The independent game-time
deadline is checked as well. Restart/replay preserve pending decisions and cannot
renew consent, duplicate resource spending or roll again for an exact retry.

### Compatibility and remaining integration

Absent adjudication policy, the Wave 7 configuration digest is byte-for-byte
unchanged; missing ruling fields load with empty defaults. Existing campaigns
therefore continue without an implicit rules upgrade. Enabling/changing policy
changes the digest and is rejected until an explicit #18 migration is implemented.

The service currently relies on the same independently authenticated caller
identity boundary as Wave 7. Authenticated HTTP routing and perspective-safe
query/stream exposure belong to #19 **in this same Wave 8 PR**. Do not expose
`play_json` or raw ruling checkpoints through legacy public projections.

### Validation at this checkpoint

Python 3.12: 107 tests pass, with five PostgreSQL contracts skipped locally because
there is no PostgreSQL daemon. CI's existing Python 3.12/3.13/3.14 matrix provisions
PostgreSQL 17 and executes those contracts, including the new adjudication test.
The new suite covers bounds, forged fields/identities/checkpoints, stale consent,
policy/player/GM authority, nonmechanical narrative, competing decisions, restart,
eight concurrent exact retries, actual dice, immutable event history, and replay
across a snapshot. Strict mypy, Ruff, no-explicit-Any, injected gate probes and
the frozen lock pass. Branch-aware suite coverage is 82.23% against the unchanged
65% requirement. The sdist/wheel build and isolated installed-wheel HTTP smoke
also pass. Local verification does not substitute for the completed CI matrix.
