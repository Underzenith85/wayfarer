# Wave 12 — setup lifecycle and reviewed voice

Implements the engine-owned work for #24 and #40. The dedicated UI roadmaps #56
and #57 remain separate acceptance gates; this PR does not close them or the
full-adventure release gates #42/#59.

## New games

`create_campaign_app(access, credentials, scenario_templates=(graph, ...))`
installs the authenticated setup service alongside frozen `/api/v1`. Supply
public, authored `ScenarioGraph` templates compatible with the server's pinned
catalog and campaign policy. These templates are authoring material: never put
another active campaign's private snapshot into the template catalog. A server
with an LLM provider can also generate a scenario from a saved brief. Without a
provider, template selection, party editing, activation and play still work.

The dashboard's **Campaign setup and lifecycle** section supports an empty
account: authenticate, save a premise/genre/tone/duration/difficulty/boundaries,
select an adventure and its party, edit purchase amounts, save the draft,
assign characters, confirm readiness, activate, and open the playing scene.
Rules are selected by the server's pinned catalog/policy, displayed in setup,
and cannot be replaced by a generated graph. Invalid or manually unapproved
builds cannot become ready. The existing character workshop remains available
for richer character diagnostics; #56 owns its dedicated onboarding experience.

Players use distinct bearer credentials from the existing authentication
configuration. Hosting a lobby grants setup authority only, not global GM
powers. The host invites an exact player principal; that player sees the
invitation on their own account, accepts it, and confirms readiness. The host
assigns each PC to exactly one joined player. NPCs cannot be claimed. All
invited seats must be joined, assigned and ready before activation. Controller
assignments are locked after activation; reassignments require a future explicit
control-transfer workflow, never a silent replacement. Players cannot alter
other players' readiness or grant themselves characters.

Setup states are draft → ready → active ⇄ paused → completed → archived.
Editing the scenario/party clears assignments and readiness. Inviting or
assigning players clears readiness. Completion requires an engine-determined
terminal objective outcome. Pausing or completing waits for in-flight director
turns; encounter decisions survive pause/resume. Archived campaigns are readable
and reject gameplay. Adventure continuation and epilogues remain Wave 13.

A setup is a private `setup_json` checkpoint in the existing campaign store.
Every edit, invitation, acceptance, assignment, readiness and transition uses
the store's command receipt and expected campaign revision. Activation creates
the pinned graph, legal characters, memberships, subgroup state, resources and
opening-scene discoveries inside the same transaction that changes lifecycle.
Concurrent duplicate activation returns the same result; a different activation
cannot reset a started game. Generation runs outside transactions and its final
write uses the saved revision. Failures and late replies leave newer work intact.

The host can read their authored graph. Other invited players receive the public
brief and seats, never scenario facts, NPC plans, hidden rewards or another
player's transcript. After activation, v1 projections reconstruct the saved
scenario engine independently for each campaign. Lifecycle checks run again
inside v1's authorized domain transaction. Draft lobbies stay out of v1's active
campaign projections and are available through `/setups`.

## Setup HTTP surface

These are additive engine endpoints, not changes to the frozen v1 schema.
They use the same bearer credentials and no-store response policy.

| Method/path | Purpose |
| --- | --- |
| GET `/setups` | Own games and principal-bound invitations |
| POST `/setups` | Idempotent creation: `id`, `brief` |
| GET `/setups/templates` | Public authored starting adventures/parties |
| GET `/setups/{id}` | Authorized lobby and revision |
| POST `/setups/{id}` | `SetupCommand`: identity, expected revision, operation |
| POST `/setups/{id}/generate` | Provider-backed proposal from the saved brief/party; registered only with provider configuration |

`SetupCommand` operations: edit, invite, join, assign, ready, activate, pause,
resume, complete, archive. Strict Pydantic models are in `simulation/setup.py`.
Host edits may save an invalid draft; readiness and activation enforce the
scenario studio's compatibility/legality checks. New game creation uses a
principal-plus-command deterministic identity. Browser write failures retain the
original request for **Retry original setup request**; **Reload games /
reconcile** fetches canonical state before allowing a replacement operation.
Credentials and unreviewed speech are never persisted by this feature.

## Voice and text

The browser adapter uses the [Web Speech API](https://webaudio.github.io/web-speech-api/).
Recognition is feature-detected (`SpeechRecognition` or `webkitSpeechRecognition`),
requires a secure origin and microphone permission, and may use the browser
vendor's remote speech service. It is not provided by the Codex subscription.
Browsers without recognition, including environments that disable it, retain
the full text interface. Target browser families are Chromium desktop/Android
and Safari where recognition is exposed; support is detected at runtime, not
promised from a user-agent string. Firefox/unsupported and denied permissions
show the text fallback. Speech synthesis is independently feature-detected.

Start the microphone, stop to review the editable transcript, then use it in the
normal text composer and explicitly send. Partial transcripts are labelled.
Recognition never submits a command. Text and reviewed speech enter the same
PlayStore, authorization/version checks and durable server action receipt.
Stopping narration changes only browser audio; it never cancels a committed
command, restarts resolution or sends cancellation to another player's scene.
Starting capture interrupts local narration. Campaign, scene, character or
membership changes, disconnect, hidden tab and logout stop capture/audio and
fence late callbacks. Separate player contexts have independent speech sessions.

Narration is opt-in and uses only the active perspective's completed narration.
No audio, interim transcript or speech-provider callback becomes canonical game
state. Completed text remains visible if speech is unavailable.

## Verification

`tests/test_wave12.py` covers atomic/repeated activation, invalid/stale edits,
separate credentials, privacy, restart and subgroup initialization, generation
failure/late replies/retries, completion/archive and v1 pause races. Existing
v1 and director suites cover receipt recovery and private projections.

Frontend voice tests cover partial review, late callback fencing, unsupported
and failed input, peer isolation, interruption and typed-intent parity. Setup
client tests preserve the exact request across lost acknowledgements. The live
Playwright suite adds two browser contexts performing setup and reviewed speech
against the real Python API with a deterministic provider and microphone fake.
Real microphone hardware/vendor transcription accuracy are manual checks. The
local runtime lacked Chromium and its CDN download timed out; browser execution
is left to the repository's existing CI gate, without expanding its matrix.
