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
The older direct `FixtureTransport` remains a focused store unit-test double.

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
