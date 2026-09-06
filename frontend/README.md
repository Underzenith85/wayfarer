# Wayfarer frontend

Issue #51 establishes the independent player shell. It does not replace the Python-served prototype or claim live gameplay integration. Python source, uv and packaging remain unchanged.

## Local workflow

Use Node 22+ and pnpm 11.19.0 (`npm install -g pnpm@11.19.0`). From `frontend/`:

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

## Boundaries

`src/api/adapter.ts` defines an injected, abortable, read-only presentation adapter. The default returns no selected campaign. Production integration must map validated, authorized versioned API responses into this view model; this interface is not an HTTP DTO or an invented endpoint. Pass an adapter once when mounting `App`. TanStack Query owns asynchronous server state. The shell includes pending, empty, retryable error and paused/offline presentation. No game rules, dice, character calculations, fake inventory, or simulated GM are implemented. API contract work and feature issues #52/#53 will extend this seam. Never put provider secrets in Vite environment variables.

TanStack Router owns Play, Character, Inventory, Journal and Campaign routes. Navigation focuses the page heading; the skip link targets main. Desktop uses navigation / scene / character columns, tablet uses navigation / scene, and phone uses fixed five-destination navigation. Radix-backed shadcn-style Button and Sheet primitives live in `src/components/ui`; the sheet handles focus trapping, Escape and restoration. `components.json` records shadcn conventions. Light/dark semantic CSS tokens, 44px controls, safe-area spacing and reduced-motion rules are shared. Only the theme preference is stored locally. UI strings are text, including narration; no HTML is trusted.

RTL exercises adapter loading, empty, failure/retry, narration and offline behavior. Playwright covers all routes, overflow at phone/tablet/desktop widths and enlarged text, keyboard sheet behavior, focus transfer, skip navigation, theme persistence and offline recovery. Live authentication, campaign selection, mutation/revision handling, streaming and GM interaction remain integration work.
