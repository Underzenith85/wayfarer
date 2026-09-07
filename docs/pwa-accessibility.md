# Installable app, offline behavior, and accessibility

Serve `frontend/dist` at the origin root over HTTPS (localhost is allowed for
local testing). Serve `/sw.js` with revalidation (`Cache-Control: no-cache`),
JavaScript MIME type, and no redirect. Deploy the complete build atomically and
retain old hashed assets while existing clients finish. Never rewrite API errors
to the SPA entrypoint. Development/fixture mode does not register this worker.

The manifest supplies standalone launch and 192/512px maskable icons. Install
through the browser menu; on iOS use Safari's Add to Home Screen. No custom
install prompt is required.

Only the build-owned JS/CSS, icons, manifest, and static index enter Cache
Storage. API requests, credentials, campaign responses, narration, and images
are not runtime-cached. Offline reload opens the connection shell; private scenes
are deliberately not persisted. An already-open scene retains its last received
state, labels its synchronization time, saves scoped drafts, and blocks actions
until reconnect reconciliation completes. There is no background action queue.

Updates install alongside the current worker. A status message asks the player
to finish pending actions and close **all** tabs/windows before reopening. There
is no forced reload, skipWaiting, or client takeover. Activation removes only old
Wayfarer shell caches. Drafts are unaffected. End session, in the play header's
**Session** menu with Switch campaign and New game, clears the principal's private
drafts, resume pointer, and query state and reloads the production app to release
in-memory credentials. Revocation uses the same private-state cleanup; the retained
setup session is dropped on session termination so it cannot retain a hidden token.
Leaving play through that menu preserves the authenticated setup session and
reopens the campaign that was being played.

## Verification targets

| Target                                            | Automated coverage                                                                                              | Manual release check                                                                |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Chromium desktop                                  | Production worker install, offline reload, reconnect, private-cache exclusion; existing gameplay/keyboard suite | Browser-menu install, two-tab upgrade                                               |
| Chromium mobile emulation (390px), tablet (820px) | Existing responsive shell/gameplay suite                                                                        | Real Android install and touch/zoom                                                 |
| Safari on iOS/macOS                               | Not verified in this environment                                                                                | Home-screen install, offline/reconnect, two-window update, VoiceOver                |
| Firefox desktop                                   | Not verified in this environment                                                                                | Shell offline/reconnect, keyboard and NVDA; installation depends on browser support |

Run `pnpm exec playwright test --config playwright.pwa.config.ts` for the real
production worker (CI includes this separately from MSW fixture tests). Run
`pnpm test` for scoped draft recovery, revocation, offline action rejection, and
bounded history regression coverage. `pnpm test:e2e` covers gameplay, navigation,
focus transfer, Escape/focus restoration, and responsive layouts.

For an upgrade, keep two installed windows open on build A, deploy B, reopen a
third window and verify A remains active while B waits. Check the update status,
close all windows, and reopen: B should activate, old shell caches disappear,
and unsent drafts remain. Repeat after an interrupted installation; A must remain
usable. After logout/revocation, check that the principal's drafts/resume pointer
and private UI are gone, including after reconnect.

## Accessibility and performance budgets

The existing skip link, semantic navigation, route heading focus, modal focus
trap/restoration, labeled controls, action status announcements, and reduced
motion rules remain the baseline. Inputs/selects now have 44px minimum height and
16px text; buttons already have 44px touch targets. Field width follows content
type rather than the content column: single-line text inputs and selects cap at
30rem, textareas at 40rem, and numeric steppers at 6rem, each still bounded by
its container. Search and filter controls are rendered only when the collection
they filter has content, so an empty inventory offers no filters. Header wrapping
supports narrow screens and text zoom. Both themes use explicit focus and
contrast tokens. Manual audit must cover new game → character → play →
inventory → journal → end session with keyboard and VoiceOver/NVDA, at 200%
zoom, in both themes. Native screen-reader and real-device sign-off is still
required; automated semantics checks do not establish WCAG conformance.

History uses accessible bounded pagination instead of variable-height virtual
scrolling: at most 50 transcript rows mount for a 10,000-entry history, with
announced ranges and keyboard-operable older/newer controls. This avoids dropping
focused content during scroll. Regression tests enforce the row budget. Target
page-change latency is under 100ms on a representative mobile device (manual
measurement required). Production JS gzip budget is 250KB and CSS 10KB; the
current build is approximately 205KB/6KB. No horizontal overflow is permitted at
390px/820px/1440px. Artwork and tactical maps are follow-up scope.
