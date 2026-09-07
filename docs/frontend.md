# Frontend development

The independent player interface lives in `frontend/`. It uses React, Vite,
TypeScript 7, Vitest, MSW, and Playwright. It can build and navigate without a
backend; live authenticated transport integration is tracked separately.

## Requirements

Use pnpm 11.19.0 with one of the Node.js ranges declared by
`frontend/package.json`:

- Node 22.22.2 or later within Node 22
- Node 24.15.0 or later within Node 24
- Node 26 or later

Corepack can activate the repository-pinned pnpm version:

```bash
corepack enable
corepack prepare pnpm@11.19.0 --activate
```

## Local development

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Vite listens on <http://127.0.0.1:5173>. Production assets are written to
`frontend/dist/`; the production host must fall back to `index.html` for
client-side routes.

Normal mode deliberately has no fabricated live backend. Enable the explicit
sample transport for local UI exploration:

```bash
VITE_PLAY_FIXTURES=true pnpm dev
```

Open `/campaign` and select a sample campaign. Query parameters select scripted
acceptance journeys; they are deterministic fixtures, not game rules or an AI
simulation. The detailed fixture catalog and live-integration caveats remain in
[`frontend/README.md`](../frontend/README.md).

## Validation

Run the standard frontend checks from `frontend/`:

```bash
pnpm contracts:check
pnpm fixtures:check
pnpm typecheck
pnpm lint
pnpm format:check
pnpm test
pnpm build
pnpm exec playwright install --with-deps chromium
pnpm test:e2e
```

Use `pnpm format` to apply formatting before committing.

## Generated contracts

HTTP types, event unions, operation metadata, and mock artifacts are generated
from the committed `contracts/v1/` schemas:

```bash
pnpm contracts:generate
pnpm contracts:check
pnpm fixtures:check
pnpm contracts:gate-test
CONTRACT_BASE_SHA=<full-base-commit-sha> pnpm contracts:compat
```

The compatibility check compares frozen documents with the base commit and
rejects semantic changes inside the existing major version. Intentional breaking
changes require a new versioned contract and migration record.

## TypeScript compiler

Application type checks and builds use the native TypeScript 7 compiler exposed
as `tsc` by `@typescript/native`. The aliased TypeScript 6 package exists only
for tools that need the JavaScript compiler API and exposes `tsc6`; application
scripts do not use it.
