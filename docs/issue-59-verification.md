# Issue 59 integration verification

> Verification record for a closed milestone. The named evidence below remains
> part of the current product gate.

Issue #59 was a verification gate over feature work owned by the UI/API and engine
issues. It did not duplicate those implementations. This document records the
browser and service evidence that CI preserves for its closed Stage A and Stage B
acceptance.

## Stage A: two-player playable slice

The required CI evidence is intentionally split across fixture and live-service suites:

| Acceptance | CI evidence |
| --- | --- |
| Two authenticated identities create/join a party, assign characters, become ready, activate, and play | `frontend/live-tests/setup-voice.spec.ts` — `two identities activate a saved party and review speech through the live director` |
| Reviewed voice/action is submitted exactly once and authoritative state changes are observed | same live journey; it asserts no write before review, exactly one `/actions` write, and the authoritative objective outcome |
| Server payload isolation between players | same live journey; Bob receives only actor `b` and no Alice director entries |
| Disconnect/reconnect without inventing or duplicating state | `frontend/live-tests/capture.spec.ts` — `independent captive and rescuer choices survive reconnect and reunite privately` |
| Split/captive/rescuer private choices and reunion | capture journey above |
| Keyboard-accessible inventory interaction and focus restoration | `frontend/tests/inventory.spec.ts` — `keyboard item controls inspect and restore focus` |
| Exactly-once item retry semantics and authoritative inventory refresh | `frontend/tests/inventory.spec.ts` — `use retry consumes once and updates the current sheet` |
| Desktop and mobile live journeys | `frontend/playwright.live.config.ts` runs `desktop` and `phone` projects on pushes; pull requests retain the desktop gate for cost control |

`scripts/check_browser_evidence.py` names these journeys explicitly. A green report containing unrelated browser tests is therefore insufficient to satisfy the release gate.

## Stage B: full campaign integration

The reference-provider suite preserves the end-to-end campaign evidence that has already landed:

- `reference adventure: reviewed voice, negotiation, saved epilogue and successor` verifies reviewed voice, deterministic narration/action handling, clarification, private payload isolation, ending, reward/conclusion persistence, and continuation.
- `reference rescue: separate players coordinate, reconnect, reclaim gear and reunite` verifies split play, capture, rescue, reconnect, item recovery, reunion, success, and private clue isolation.
- `frontend/startup-tests/new-game.spec.ts` preserves the clean-database production New Game path required by #81, including no pre-existing campaign ID or fixture-mode startup.

The CI browser-evidence gate requires those named reference and startup journeys as well.

## Guided authoring completion

Issue #84 subsequently delivered guided generation and refinement. Deterministic
provider tests in `tests/test_catalog.py` cover proposal review, save/reopen,
export/import round trips, immutable exact-revision launch, restart recovery, and
launch from saved content without asking the provider to recreate it.

`frontend/startup-tests/catalog.spec.ts` exercises the production UI and service:
an author saves a portable document, reloads it, publishes the exact revision,
creates a game from it, and starts play without exposing private author notes.
`scripts/check_browser_evidence.py` names that journey so deleting it cannot leave
the product browser gate green. The separate provider-backed service tests retain
the deterministic AI boundary without requiring live credentials in CI.
