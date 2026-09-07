# Wave 13: adventure conclusions and campaign continuation

Issue #41 extends the authenticated `/setups` lifecycle from Wave 12. Completing
an engine-determined adventure stores its immutable scenario and final play
snapshot in the same revision-checked transaction. Success, partial success,
failure and abandonment end an adventure; none ends the campaign automatically.

The setup dashboard presents recorded objective evidence, discoveries, casualties,
commitments, settled rewards, advancement and injury pools. These are deterministic
projections, not model-authored canonical consequences. Facts are filtered by the
viewer's controlled actors' knowledge; private objectives, rewards and resources
retain their audiences. Hidden commitments are not revealed by another actor's
knowledge. Historical snapshots never change during previews or continuation.

## Commands

All commands use the existing authenticated `POST /setups/{id}` envelope with
`id`, `expected_revision` and `operation`. Host authority is required.

- `complete`: active → completed, after a terminal objective outcome, resolved
  combat, queued actions and director turn.
- `preview`, with `graph`: validate and save a successor while remaining completed.
  The dashboard supports authored successors and provider-generated successors.
- `POST /setups/{id}/generate`, operation `preview`: generate from the saved brief,
  current party and lasting state. Provider calls run outside transactions; late
  replies fail revision checks. Failures preserve existing state and previews;
  successful retries use the durable generation receipt without another call.
- `continue`: revalidate the saved preview and atomically activate it in the same
  campaign. A distinct adventure and objective namespace is required, and settled
  reward IDs cannot be reused. Concurrent retries activate once.
- `archive`: completed → archived. `unarchive`: archived → completed. Resuming
  archived games never reopens or re-awards the concluded adventure.

Continuation retains builds, approvals, HP/FP, conditions, inventory, advancement,
resource schedules and receipts, shared time, world facts, beliefs and commitments.
Existing canonical records win over draft values. Starting inventory cannot
recreate spent items, and new party equipment requires reward/resupply mechanics.
The next graph must support carried recovery records; it cannot relocate a captive
without release. Fresh NPC plans and objective deadlines use absolute shared time.
Scenario-local encounter/director/scene progress starts anew and remains available
in the archived adventure snapshot. New player/NPC roster changes are outside this
continuation contract: the graph must preserve existing actor builds.

Recovery/advancement summaries are visible in the conclusion; mechanical downtime
continues through authored recovery actions during active play, including the next
adventure. Continuation does not grant a free rest or spend advancement points.
The frozen `/api/v1` contract is unchanged; the richer #58 presentation track can
consume these live setup/lifecycle capabilities.

## Verification

Feature contracts cover all three principal ending outcomes, immutable snapshots,
injury/inventory/advancement preservation, private projection, rejected duplicate
rewards, concurrent continuation, restart, generation receipt reuse, HTTP identity
checks and archive restoration. The React test covers the saved conclusion and
archive restoration controls. Existing setup tests cover failed/late generation
and revision conflicts. PostgreSQL-specific transaction tests remain in CI.
