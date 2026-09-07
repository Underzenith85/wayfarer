# Wayfarer frontend

Issues #51/#52 establish the independent player shell and campaign play workspace. It does not replace the Python-served prototype or claim live gameplay integration. Python source, uv and packaging remain unchanged.

## Local workflow

Use Node 22.22.2+, 24.15+ or 26+ and pnpm 11.19.0 (`npm install -g pnpm@11.19.0`). From `frontend/`:

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm typecheck
pnpm lint
pnpm format:check
pnpm test
pnpm build
pnpm exec playwright install --with-deps chromium
pnpm test:e2e
```

Vite serves localhost:5173. Production static files are in `dist/`; configure the serving host to fall back to `index.html` for client routes. No backend or credentials are needed to build or navigate. Run `pnpm format` before committing. Frontend CI runs alongside the existing Python workflow, including three browser viewport projects.

## Compiler

Type checks and builds use the stable TypeScript 7 native compiler (`pnpm exec tsc --version`). `@typescript/native` aliases `typescript@~7.0.2`, which supplies `tsc`. The `typescript` dependency aliases `@typescript/typescript6` solely for tools such as typescript-eslint that require the JavaScript compiler API; its executable is `tsc6` and is not used by build or typecheck scripts. This follows [Microsoft’s side-by-side setup](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/#running-side-by-side-with-typescript-6-0).

Path aliases are relative to the config without the removed `baseUrl` option. Node ambient types are explicit for Vite and Playwright configuration. Strictness checks remain enabled.

## Campaign workspace preview (#52)

```sh
VITE_PLAY_FIXTURES=true pnpm dev
```

Open `/campaign` and choose a sample story. The fixture switch is off by default;
normal builds retain an unconfigured connection and never pretend the backend is live.
To build the explicit sample preview, use `VITE_PLAY_FIXTURES=true pnpm build`.
Sample outcome selection is a developer query parameter at initial page load:
`/campaign?journey=clarify` (also `resolve`, `reject`, `retry`,
`narration-failure`, `expired`, `stale`). Inputs do not determine outcomes:
these are deterministic response scripts, not game rules or an AI simulation.
Fixture sessions reset on reload; unsent local drafts persist, keyed by principal,
campaign, scene, actor and channel. Browser storage may be disabled without
blocking in-memory drafting. Shared devices should end the authenticated session
before changing users; expiry clears that principal's drafts and resume marker.

The workspace includes campaign selection/resume, scene observations, recap,
known-objective and visible-party presentation, three distinct message channels,
contextual inspect controls, clarification choices/text, action status and
expandable authoritative rolls/consequences. Character/inventory summaries update
from a new snapshot after a committed result, never from narration. A narration
failure keeps the successful result. Unknown acceptance retries the identical
command and body. Stale-version responses require reload and reconsideration.

`src/play/transport.ts` is the injected application facade over the frozen HTTP
DTOs. The runtime has no fabricated HTTP routes or credentials. The store fences
all asynchronous responses, aborts polling/narration and clears Query caches when
switching campaigns, and clears private state on authorization/session failures.
It restores action history and pending choices when re-entering a campaign;
nonterminal actions recover through polling at no faster than one second. The
fixture adapter can use shorter delays in unit tests. Existing #51 read-state
components remain independently tested for loading/empty/error/offline displays.

## Generated clients and shared mocks (#49)

`src/api/client.ts` exposes the typed OpenAPI client for all frozen v1 operations.
`src/api/live.ts` exposes a typed, bounded WebSocket iterator with runtime frame
validation, scope fencing and abort cleanup. `NetworkPlayTransport` implements
`src/play/transport.ts` using both. Pass the service origin, authenticated principal
and credential when configuring a real connection; backend runtime/auth integration
remains #50. Automatic reconnect, replay application and cache invalidation policy
remain #54. The transport rejects an inconsistent snapshot handoff so callers can
retry without publishing mixed state.

Sample mode now starts MSW HTTP handlers and a WebSocket adapter, then uses this
same network transport. It needs no server, AI provider or real credential. MSW
loads only with the explicit sample switch. Unknown API requests fail locally.
The direct `FixtureTransport` remains a focused store unit-test double and the
base for the separate inventory preview described below. A recognized `inventory`
selector takes precedence over `journey` and selects `InventoryFixtureTransport`
without starting MSW. Unrecognized inventory selectors fall back to the shared
MSW journey. This preserves the proposed-operation preview until its contracts freeze.

Alongside the original journeys, `?journey=` accepts `join`, `legal-character`,
`illegal-character`, `item-use`, `encounter`, `split`, `capture`, `rescue`,
`reconnect`, `ending`, `revoked`, `duplicate` and `out-of-order`. These are scripted
contract scenarios, not a second rules engine. Domains whose dedicated endpoints
are still proposed use existing scene/character/action/error projections; this
change does not freeze or invent their later request DTOs. `createMockHandlers`
accepts a scenario, origin and latency; use `fixtureCredential('player-2')` to
exercise a rescuer's separate view or `fixtureCredential('gm')` for invite tests.
The fixture catalog and network tests show scripted transitions and fault timing.

Generated HTTP types, event unions and operation metadata come only from committed
`contracts/v1` schemas. Responses, request bodies and live frames are validated
with those same JSON Schemas. All scenarios, principals and committed/initial
projections are schema-tested, as are the committed live transcript examples.
Snapshot objective/party fields remain presentation mappings, not wire additions.

```sh
pnpm contracts:generate     # HTTP/event types, operation metadata, MSW worker
pnpm contracts:check        # fails on generated drift
pnpm fixtures:check         # rejects schema-invalid fixtures
pnpm contracts:gate-test    # tests the compatibility guard
CONTRACT_BASE_SHA=<full-PR-base-SHA> pnpm contracts:compat
```

The compatibility gate compares all frozen documents against the PR base commit.
It conservatively rejects semantic changes, including additions, inside an
existing major. Formatting and textual documentation corrections are allowed.
For an intentional change, retain v1, add a new `contracts/v2` surface and include
`contracts/migrations/v2.json` with `from_version`, `to_version` and a substantive
`reason`; review the migration and consumer updates in the PR. The record cannot
bypass preservation of existing contracts. Generation stays on v1 until consumers
explicitly migrate. CI fetches base history and runs this guard on pull requests.

The pnpm lockfile and packageManager pin the toolchain. openapi-typescript still
advertises a TS5 peer range; the existing scoped exception allows its tested TS6
API shim, while TypeScript 7 remains the application compiler. MSW's optional
postinstall reminder is disabled; worker generation is explicit and drift-checked.

CI runs generated drift, compatibility, fixture validation, typing, lint,
formatting, unit/network tests, builds and browser tests. Playwright exercises the
shared MSW transport across phone/tablet/desktop, including clarification, exact
retry, narration failure after commit, campaign switching and expiry.

## Character and inventory preview (#53)

From `/campaign?inventory=inventory` in sample mode, open a campaign then choose
Character or Inventory. The character page reads HP/FP, conditions, attributes,
skills, defenses, movement and fixture-provided derived effects and advancement
ledgers. Values and point balances are displayed, never calculated or spent by UI.
Inventory includes search/location filters, item details, quantities, unit weights,
server-reported carried weight/encumbrance, integer-minor-unit currency display,
known containers and permitted ownership/custody descriptions.

The item sheet confirms operations explicitly, validates integral quantity/known
options, presents unavailable-action reasons, and shows server rejection/retry
feedback inside the modal. Frozen inspect/use requests use v1 `SubmitAction`.
Equip/drop/transfer/store are isolated behind `inventoryPreview` and only enabled
in explicit sample transports. They never enter the frozen closed Intent union.
`src/character/presentation.ts` records these proposed presentation shapes and
preview commands; promotion to a live contract and #49 integration remain required.
This PR does not close #53's dependency or the #8/#12/#18/#23 live acceptance gates.

Request receipt identity survives exact retries. Pending work disables repeated
confirmation. No local quantity/weight/HP deltas are applied: successful actions
trigger authorized snapshot reads. A stale-version response blocks further writes
until **Review changed inventory** reloads the projection; it never auto-resubmits.
Contextual item actions preserve unrelated unsent play drafts. Confiscated items
retain known identity with no invented hidden custodian/container/location; their
mutation affordances stay disabled until an authorized recovery projection arrives.

Inventory fixture journeys (initial-load `inventory` query parameter):

- `inventory`: fixed success responses for inspect/use, equip the sword, drop the
  coat, store one bandage in the satchel, or transfer one bandage to Sera. Reload
  between independent fixture cases; these snapshots are not a persistent rules engine.
- `illegal-equip`, `full-container`, `invalid-container`, `remote-transfer`: a
  formerly available request is rejected after current-state validation.
- `use-retry`: lost acknowledgement; exact retry consumes the one fixture bandage once.
- `encumbrance`: dropping the coat returns a new weight/encumbrance/movement projection.
- `capture`: known confiscated items are visible but cannot be mutated.
- `recovery`: first read is confiscated; **Refresh inventory** returns the scripted
  authorized recovery snapshot. This does not implement rescue mechanics.
- `inventory-conflict`: stale request; review reloads a changed inventory without retry.

Tests cover these lifecycle/resource boundaries, frozen-schema validity,
role/affordance restrictions, keyboard/touch operations and responsive layouts.
No new dependencies were needed for #53; the TypeScript 7 stack remains intact.

## Multiplayer scenes and recovery (#54)

Start `VITE_PLAY_FIXTURES=true pnpm dev` and open two independent browser profiles:

- `/campaign?multiplayer=captive&room=demo` controls Mara in a private cell.
- `/campaign?multiplayer=rescuer&room=demo` controls Ivo and Sera in their own scenes.

The same room joins both profiles to one development-server mock authority. The
mock identity headers are demonstration credentials only. The fixture endpoint is
registered only when fixture mode is explicitly enabled on the Vite development
server; it is absent from preview/production servers, and secret fixture data is
not bundled into the frontend. No backend or model credentials are needed.

The Scene & companions panel provides owned-character selection, scoped presence,
readiness, and explicit split/transfer/rejoin forms using only permitted
destinations. Unseen groups and captive locations are omitted by the authority
before delivery. Readiness never chooses a consequential action for a player.
Presence becomes away after six seconds without a mock heartbeat; it has no
mechanical effect. OOC chat is opt-in for display and deliberately table-wide. It
never updates character knowledge, submits a game action, or copies scene history.

Browser offline events and transport disconnection pause all authoritative
commands while retaining drafts under principal/campaign/scene/actor/channel keys.
Reconnect clears cached views, rereads the authorized snapshot and receipts, then
restarts the scoped subscription. An accepted request appears once; an unaccepted
request retains its exact identity for explicit retry. Neither reconnection nor
readiness automatically resubmits a new action. Revocation and control loss erase
private views, drafts, pending requests, and query caches. An old subscription or
in-flight response cannot repopulate another scope.

`MultiplayerPort` is a normalized adapter boundary, not a new production HTTP or
WebSocket protocol. Atomic views carry opaque cursors and visibility epochs from
one principal/scene/actor perspective. TanStack cache keys include all these scope
components. Duplicate checkpoints are ignored; any changed, gapped, reordered or
reset event causes a fresh authoritative view rather than applying speculative
deltas or sorting opaque versions. The mock adapter polls invalidations. The #49
live adapter must map #48's scoped subscriptions and atomic snapshot/ready barrier
to this interface, validate incoming schemas, and bind callbacks to their actual
subscription. Presence, readiness, group commands and OOC remain proposed
presentation contracts; they are not added to the frozen v1 schema here.

The development-only scenario driver accepts POST `/__fixtures/multiplayer` with
`x-mock-identity: captive|rescuer`, `x-mock-room: demo`, and
`{"op":"scenario","scenario":"rescue"}` to permit a scripted rendezvous. Both
players then explicitly rejoin after resolving their own pending decisions.
`missed` changes a checkpoint; `revoke` and `reassign` remove the selected mock
identity's control. Tests create unique rooms. These are fixed acceptance events,
not an alternative travel or rescue rules engine; restart Vite to reset rooms.

### Live integration gate

#45's engine work is merged, but #49/#50 transport integration remains open.
Mock completion of #54 does **not** establish live multiplayer authorization.
Before #59 signs off (with #23/#24/#40/#41 owning their integrations), run the same
two-browser scenarios against independently authenticated real principals and the
#45-backed API: verify payload-level secrecy before and after reunion, server
rejection of foreign actors/remote destinations/stale membership, independent
pending choices, heartbeat disconnect, retained/expired replay cursors, visibility
reset, control reassignment and revocation during in-flight requests, and exactly
one receipt after an accepted command loses its acknowledgement. Client filtering
is never the authorization gate. The frontend tests exercise these privacy and
recovery boundaries against the server-side fixture authority only.

### Encounter and discovery fixtures (#55)

Start the fixture server and open `/campaign?adventure=true&room=your-room`
(optionally `&multiplayer=rescuer`). Open the campaign to begin a pending combat
**defense**, then social, investigation, stealth and hazard decisions. Select a
permitted target; each option includes a rules trace and known range. The server
retains the pending decision across reloads and rejects stale versions, invalid
targets and conflicting duplicate command IDs. Unknown submission outcomes offer
an exact-command retry; reconnect reads the authoritative state before enabling
controls. No tactical map or inferred hidden targets are shown.

The Journal searches known NPCs, locations, clues and commitments, displays known
objective progress and a scoped what-changed recap. “Mark recap read for next
visit” saves an opaque checkpoint in session storage for that principal, campaign,
scene, actor and visibility epoch. An unknown checkpoint resets to the permitted
recap. Entry links include campaign and actor context and recheck access on load;
unknown, undiscovered and other-character entry IDs all return unavailable.
Search filtering happens in the server-only authority before results are returned.
Revocation and perspective changes clear the existing play/query scope.

`src/adventure/model.ts` is a normalized **proposed** adapter boundary, not a new
frozen HTTP contract. `fixtures/adventure-authority.ts` scripts outcomes; it is not
the live rules engine. Existing MSW journeys and production connections retain
their prior behavior. Live wiring remains gated by #16, #17, #34, #35 and #36,
with integrated acceptance owned by #23/#24/#40/#41 and #59. No dependency changes
were needed; this retains main’s TypeScript 7 compiler and dependency versions.

## Presentation boundary for engine identifiers (#159)

`src/presentation/labels.ts` is the single mapping from engine addressing to
player-facing text. Engine keys (`attribute:st`), lifecycle enum values (`pause`),
campaign phases, conditions and load bands are resolved there and nowhere else,
so no raw identifier reaches a reader. Attributes render in canonical GURPS order
(ST, DX, IQ, HT) with the short label shown and the full name — Strength,
Dexterity, Intelligence, Health — as the tooltip and accessible name; ordering by
the key itself would sort them alphabetically and break recognition. Statistics
the mapping does not name keep an authored label when the service supplies prose,
and otherwise fall back to a readable form of the key.

Encumbrance is a display of the band the projection reported. The v1 projection
marks the category unavailable rather than guessing it (see
docs/api-v1-runtime.md), so the UI says **Encumbrance not reported** instead of
printing the placeholder it received.

Version digests are engine bookkeeping, not player information. The character
sheet, inventory page and the At a glance rail keep them inside a collapsed
**Technical details** disclosure (`src/components/technical-details.tsx`) rather
than beside HP and FP.

Setup lobby seats render as structured rows — player, assigned character,
readiness — instead of a joined record string, and host lifecycle controls are
labelled with the action taken (**Pause session**, **End campaign**) rather than
the operation name sent to the service. The party editor labels each purchase by
its definition name (`attribute:st` reads Strength) instead of the catalog key
the input sends.

The setup service sends a `party` roster — the assignable characters' ids and
names — beside the seats, so seat rows, assignment options, the party editor's
legends, recorded casualties and the saved-conclusion recovery pools all read a
character name. `poolLabel` splits a runtime pool id (`hp:b`) into that name and
the pool it holds. The roster is deliberately narrower than the graph: only the
host may read the graph, and NPC identities stay behind that gate, so a name here
reveals nothing about the scenario. When a name is missing the mapping still falls
back to a readable form of the identifier rather than printing it raw.

## One “At a glance” per width (#161)

The summary is offered once at any viewport width. Above 1100px it is the
persistent rail in the right column (`aside.character-panel`); at 1100px and
below the rail is hidden and the same `CharacterSummary` is reached through the
**Details** drawer in the page heading. The drawer's trigger carries
`.compact-only`, whose one rule pair sits beside the rail's own breakpoint in
`src/styles.css`, so the trigger appears exactly where the rail does not — a
modal never dims the page to repeat what is already beside it. `tests/shell.spec.ts`
asserts the exclusivity on both sides of the breakpoint, and a test that wants
the summary reads it from whichever presentation the viewport has.
