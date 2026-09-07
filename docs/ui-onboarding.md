# Campaign onboarding and character workshop (#56)

This is the frontend contract/mock delivery. Live acceptance remains with
#21, #22, #40 and #59 Stage B. No production endpoints, authentication flows,
LLM calls, or frozen v1 schemas are added by this change.

Run `VITE_PLAY_FIXTURES=true pnpm dev` in `frontend`, then open
`/campaign?onboarding=true&identity=host&room=my-table` and
`/campaign?onboarding=true&identity=guest&room=my-table` in separate tabs or
browser contexts. These are explicitly simulated identities, not credentials.

The host chooses name, premise, tone, duration, difficulty, pinned rules,
party relationship and scenario. Create an invitation and copy its code to the
guest's join form. Invitations are single-use; regenerating one invalidates the
old code. Refresh the lobby to reconcile another player's changes. Only the
host may issue invitations, assign unclaimed slots, approve builds or start play.
Each member can claim one available character and edit/finalize their own build.

The workshop preserves saved drafts and their revision history in the shared
Vite authority, including page reloads. Unsaved editor text survives lobby refresh
and errors but not page reloads. Loading a saved revision explicitly discards
unsaved edits. Save and validate or simulate generation to review the saved
revision. Generation has legal, over-budget and failure fixtures.
Failures preserve the saved revision and the current editor. Editing an approved
draft resets approval. Finalization requires the exact approved revision.

The limited mock catalog supports ST and DX (IQ/HT fixed at 10), a 100-point
budget, attribute limits 8–14, and a 25-point reduced-attribute cap. These checks
are deterministic authority-side fixture behavior, not a replacement for the
live engine compiler. Both members must finalize a character and mark ready
before activation. Replayed command IDs apply once; changed payloads, stale
lobby/draft revisions, and new duplicate activation commands are rejected.

Guests receive only their own draft/history and the public scenario preview.
The opening scene uses the selected scenario and campaign premise. This journey
ends at the opening scene; actions are unavailable on this fixture connection.
The new `OnboardingPort` is a presentation interface, not a proposed HTTP API.
The fixture endpoint exists only in the Vite dev server with the explicit fixture
flag. Its rooms are memory-only and reset on server restart (at most 100 rooms).
Production builds do not include the authority or mock character generator.

## Verification

- `pnpm test`: membership, ownership, illegal builds, approval invalidation,
  generation recovery, revisions, private projections and exactly-once activation.
- `pnpm test:e2e tests/onboarding.spec.ts`: two identities from empty account
  to opening scene, invalid build, failed generation, reload recovery and stale
  edits. The standard Playwright projects cover phone, tablet and desktop.
- `pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm build`.
