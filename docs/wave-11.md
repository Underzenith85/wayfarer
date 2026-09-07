# Wave 11: durable direction and authoring

Wave 11 adds the character workshop (#21), scenario studio services (#22), durable
turn director (#39), and live integration in the existing React dashboard (#23).
The frozen v1 contract remains separate; `LiveTransport` explicitly adapts the
existing authenticated engine facade. It does not impersonate the v1 API.

## Director

`DirectorService.run` persists actor/principal identity, subgroup session binding,
original input, the validated interpretation, and phase before executing a domain
command. Both text input and explicit UI proposals use the same pipeline. A caller
retries with the same turn ID and input. Stored domain receipts recover a crash
between resolution and the next director checkpoint without repeating rolls,
time advancement, discoveries, or rewards. Late provider responses use campaign
revision CAS. Changed context returns clarification instead of silently rebasing.

Domain transactions retain their existing ordering: resolution, conservative
shared-time advancement, authored NPC reactions and objective settlement. The
director never replays these checkpoints itself. Deadline semantics remain those
of `ObjectiveRules` (failure and deadline precede simultaneous success). The
phase loop is bounded, as are provider retries and the existing NPC cascade.

Narration uses scoped visible state and committed receipt metadata. Narration
failure commits a text fallback without undoing mechanics. Subgroup changes
invalidate old session bindings; pending actions and private turn histories are
filtered to controlled actors. Metadata checkpoints preserve valid adjudication
consent without advancing game time. Existing domain decisions remain canonical.

## Character workshop

Private drafts live in the campaign checkpoint and event log. Save, approve and
activate commands require both campaign and draft revision preconditions. Edits
clear approval; generation cannot overwrite a newer human edit. Illegal drafts
remain editable and return compiler diagnostics, point totals, purchase breakdown,
derived values, repair proposals, power findings and before/after patches.

Activation re-runs compiler and power approval validation using stored approval
records. It is restricted to setup; active characters must use the advancement
ledger, so editing a draft cannot heal a character or bypass earned points.
The existing character page supplies catalog-backed editing, generation, validation,
repair and activation controls. GM approval is available through the authenticated
draft command API; the dedicated authoring UX remains part of #56.

## Scenario studio

`ScenarioGraph` composes the actual world, resource, scene, objective, noncombat,
NPC, recovery and subgroup contracts. The same initial-state builder is used for
validation and activation. Structural checks cover references, unreachable scenes,
circular clue gates, missing mandatory evidence, single-roll/NPC bottlenecks,
contradictory terminal conjunctions, escrow reuse, party capabilities and authored
capture escape/rescue branches. Challenge dimensions are estimates, never a claim
of guaranteed fairness or solvability.

Generated parties must match the supplied party. Generation and repair are bounded.
Invalid but schema-valid candidates can be saved as private drafts; they cannot
activate. NPC player-state builds require an explicitly supplied NPC reviewer and
must also fit the runtime policy; offscreen world NPCs use the existing NPC rules.
This deliberately rejects unsupported NPC builds rather than weakening legality.

Activation inserts a new starting snapshot atomically. A retry with the same
campaign ID, graph and membership returns the existing campaign. The graph is
stored privately on the campaign so `CampaignAccess.runtime` can reconstruct its
pinned scene configuration after restart. New-game party assembly and polished
scenario authoring UI remain consumers in #40 and #56.

## Authenticated facade

- `GET /campaigns/{cid}`: scoped state, character values, equipment names,
  scene options, pending combat/noncombat decisions, director cursors and feasible
  recovery choices.
- `POST /campaigns/{cid}/interpret`: `actor_id`, `command_id`, `text`, and optional
  `proposal` for an explicit typed action. Actor and command identity are service-owned.
- `GET /campaigns/{cid}/workshop/{actor_id}`: owned draft and catalog choices.
- `GET /campaigns/{cid}/drafts/{draft_id}`: owner/GM-only draft inspection.
- `POST /campaigns/{cid}/drafts`: revisioned save/approve/activate.
- `POST /campaigns/{cid}/generate-draft`: character generation followed by CAS save.
- `POST /campaigns/{cid}/generate-scenario`: GM-only scenario generation and CAS save.
- `POST /campaigns/{cid}/scenario-validation`: GM-only structural validation.
- `POST /campaigns/{cid}/drafts/{draft_id}/activate-scenario`: GM-only activation
  with `campaign_id` and `expected_draft_revision`.

Provider routes are enabled when `create_campaign_app` receives `settings`.
Pass `frontend_dir=Path("frontend/dist")` to serve the built React application on
the same origin. The Vite development proxy forwards `/campaigns` to port 8000.
The connection form holds bearer credentials in memory and clears the input after
connecting. Reload requires reconnecting; durable decisions remain on the server.
No model subscription login is embedded in the browser.

## Validation and remaining integration gates

Feature tests inject restarts at every director boundary, exercise noncombat →
travel → outcome/reward settlement, resume combat defense, verify private drafts,
late generation rejection, illegal activation and scenario retry/reachability.
Existing split-party/capture tests continue to run. Frontend tests cover bearer
identity isolation, out-of-order reads and recovery of a persisted turn.

Run the new desktop/phone live capture-and-rescue check with `pnpm test:e2e:live`.
Its local execution was blocked by a missing Chromium binary and a timed-out
browser download; the service and adapter tests passed.

#23 remains an integration milestone: #54/#55 and the final two-browser live
capture/rescue acceptance flows must finish before closing it. This change adds
live integration to the existing dashboard; it does not close those child issues.
