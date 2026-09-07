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

## Dependency and contract gate

**Draft until #49 is delivered and integrated.** #48/#49 are still pending at the
start of this change. This work does not claim completion of their shared MSW,
WebSocket, drift/breaking-change or reconnect fixtures, nor live integration in
#23/#50/#59. The `Snapshot` objective/party fields and narration iterator are
presentation-only fixtures; they are not additions to the frozen HTTP schema.
Dialogue uses the frozen text intent with explicit in-character phrasing; OOC uses
question intent. Neither claims a separate chat endpoint. #49 should replace the
fixture facade with its generated client/shared transport and settle these
presentation mappings before #52 is closed.

Types in `src/api/contracts.generated.ts` are generated from the committed
OpenAPI contract. `pnpm contracts:generate` regenerates them and
`pnpm contracts:check` checks drift. JSON Schema tests validate campaign, scene,
character, inventory, session and all action fixtures against the frozen v1
schemas. This is a narrow prerequisite for the UI, not the full #49 contract CI.
The latest openapi-typescript CLI still advertises a TS5 peer range; a scoped
pnpm peer compatibility exception allows its tested TS6 API shim. TypeScript 7
remains the actual application compiler. No other peer ranges are relaxed.

Playwright runs sample mode across phone/tablet/desktop and covers send,
clarification, rejection, exact retry, narration failure after commit, draft
persistence and expiry, plus the original keyboard/focus/overflow journeys.
The frontend workflow runs contract drift, typing, lint, formatting, unit/schema
tests, build and browser tests alongside the unchanged Python checks.

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
