# High-speed Wait source review

Source baseline: Basic Set Campaigns fourth printing, PDF SHA256
`79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80`.
The supplied bytes and printed B366, B385 and B394–395 were reopened on
2026-10-01. B366 and B385 permit a declared Wait to interrupt the triggering
action; B394 constrains the entering turn and later high-speed trajectory.

## Bounded #771 acceptance

The checks below use `tests/test_issue_771_high_speed_wait.py` and inspect
persisted positions, high-speed state, injury, pending defenses and replay.

- Nontriggering Wait permits legal entry and established motion; a matching
  Wait pauses at its first path entry. `test_wait_allows_or_pauses_high_speed_entry`
  checks the five-yard entering move and six-yard subsequent velocity for Move 5.
- `test_wait_preserves_turning_before_and_after_checkpoint` checks the one-turn
  entry allowance and established turning distance across both sides of a
  restart. Direction and remaining distance persist; only unused movement is spent.
- `test_waiters_resolve_in_path_order_before_turn_order` checks that earlier
  trajectory triggers precede later ones. Initiative resolves ties.
  `test_multiple_waiters_react_at_the_same_high_speed_checkpoint` includes both
  an intermediate checkpoint and the final movement hex. No waiter or path is
  lost when the saved continuation has no movement left.
- `test_move_and_attack_waits_during_movement_before_attack` checks both entering
  and established Move and Attack. Movement Waits pause before the attack;
  resumption reaches the target and creates exactly the pending defense.
- `test_wait_reaction_knockdown_retires_unspent_trajectory` resolves an actual
  reaction: a seven-point cutting hit causes ten injury and a failed HT roll
  causes unconsciousness. The existing authenticated resume retires the unused
  voluntary path, settles the already-started turn once, and cannot grant it
  later. Position remains the interruption checkpoint; this is not a computed
  resting position after a fall or skid.
- `test_movement_wait_uses_first_observable_zone_entry` rejects an obscured-only
  trigger and pauses at a later visible entry in a multi-hex zone. Path ordering
  uses that same visibility-aware checkpoint.
- The same tests reject unauthorized commands and stale revisions, replay
  original and resumed receipts without new dice, and reconstruct identical
  state after restart/event replay. Invalid complete paths reject before any
  checkpoint or random draw is committed.

This repairs the existing Wait transaction without introducing command fields,
new certification status or alternate movement machinery. Final-hex continuation
is derived from the persisted interrupt inside the command context, rather than
from a caller-authored assertion.

## Remaining movement boundaries

Cancelling an unspent high-speed path rejects because an immediate stop would
bypass the separately unsupported B395 braking rules. Acceleration, deceleration,
difficult terrain, vehicle controls, falling, skids, collisions and momentum
after incapacitation remain unsupported. This work does not supply their
physical outcomes or certify every maneuver or the API/UI/LLM gates.
