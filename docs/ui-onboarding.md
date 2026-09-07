# Live New Game entry (#81)

The normal `wayfarer` launcher serves the built React UI and authoritative campaign API. Follow the [README setup](../README.md). Sign in with one access token; `/setups/session` returns the authenticated player identity and provider availability. Campaign selection never gates setup access.

The Last Beacon ships in solo and two-player variants. Setup is a stepper — Concept, Adventure, Rules, Party, Ready — that shows one step at a time with a progress indicator, so creating another draft replaces the current step view instead of appending a second form. Select a template, edit the brief and starting character purchases, save, assign characters, mark ready and explicitly start. The server's existing compiler and atomic setup activation remain authoritative. Invitations bind to distinct authenticated player names; guests never receive the host's private scenario graph.

New setup creation can include a selected graph in the same idempotent write. Pending requests retain their original payload and command ID in tab-scoped session storage. Retrying or reconciling sends the original request; a lost activation acknowledgement cannot cause a second game. Definite validation/conflict responses allow corrections; stale state must be reloaded. A refresh keeps the tab signed in and reopens the campaign the URL names, so saved drafts, pending commands and active games are reached without re-authenticating. The access token lives in that tab's session storage only: it never reaches another tab, and it is discarded when the tab closes, when the session expires, or on **End session**.

Setup and play are separate shells. Opening a campaign unmounts the launcher and the setup panel, so the game shell begins at the top of every route; the play header's **Session** menu returns to setup through **Switch campaign** (reopening the campaign that was being played) or **New game**, keeping the authenticated setup session in memory so no second sign-in is needed. **End session** lives in that menu with them.

The production-build browser suite is `pnpm test:e2e:startup` in `frontend`. It launches the normal Python entry point with temporary test credentials and no AI provider, covers desktop/phone and keyboard activation, illegal party rejection, stale edits, lost acknowledgements, refresh, and independent invitation/readiness. `tests/test_runtime.py` separately verifies draft and active-play recovery across application restarts. Existing mocked onboarding and live capture/voice suites remain below.

## Earlier onboarding presentation fixtures (#56)


This is the frontend contract/mock delivery. Live acceptance remains with
#21, #22, #40 and #59 Stage B. No production endpoints, authentication flows,
LLM calls, or frozen v1 schemas are added by this change.

Run `VITE_PLAY_FIXTURES=true pnpm dev` in `frontend`, then open
`/campaign?onboarding=true&identity=host&room=my-table` and
`/campaign?onboarding=true&identity=guest&room=my-table` in separate tabs or
browser contexts. These are explicitly simulated identities, not credentials.

The host chooses name, premise, tone, duration, difficulty, pinned rules,
party relationship and scenario. In the production lobby, the **Rules profile**
select lists registered profiles from `/setups/profiles`; unsupported profiles
are shown disabled with their unverified capability count, and the server
default applies when nothing is chosen (see [rules profiles](rules-profiles.md)). Create an invitation and copy its code to the
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
