# 10 — Implementation plan

Seven phases. Each is independently shippable, each has acceptance criteria you
can actually check, and each ends with the app in a working state.

Run `pnpm test && pnpm test:e2e && pnpm typecheck` at the end of every phase.
The suites are behaviour-based (see [09](09-migration-map.md#what-the-tests-select-on)),
so they should stay green throughout — **a red suite means you changed markup
semantics, not just styling.** That is the signal to stop and look.

---

## Phase 0 — Decide about Tailwind (blocking, ~30 min)

Tailwind v4 is installed, `styles.css` imports it, it contributes preflight plus
a dozen unused utilities, and no utility class appears in any component.
`components.json` points shadcn at `baseColor: slate`, which is not this palette.

Pick one:

- **(a) Drop it.** Remove `@import "tailwindcss"`, `@tailwindcss/vite` from
  `vite.config.ts`, and `tailwindcss` from devDependencies. `base.css` already
  carries the reset this system needs. Update `components.json` to
  `"tailwind": { "config": "", "css": "src/styles.css", "cssVariables": true }`
  and expect to hand-adjust any future shadcn component.
- **(b) Keep it and mean it.** Map the tokens into a `@theme` block so
  `bg-paper` and `text-ink` exist, and accept that two styling idioms will
  coexist for a while.

**Recommendation: (a).** Nothing in the app uses it, this system is a
hand-authored CSS layer by design, and every future shadcn install would
otherwise pull in slate-coloured defaults that have to be undone by hand.

**Acceptance:** the built CSS contains no `@layer utilities` block; bundle CSS
drops by roughly 6 KB; `pnpm build` passes.

---

## Phase 1 — Tokens and base (~1 h)

1. Copy `css/tokens.css`, `css/base.css`, `css/components.css` into
   `frontend/src/styles/`.
2. Replace `frontend/src/styles.css` with the three `@import`s from
   `css/index.css` (keep the filename — `main.tsx` imports it).
3. Self-host the four families, or keep the `@import` from Google for now and
   open a follow-up. Note the IP-leak and render-blocking cost either way.
4. Update `index.html`'s `theme-color` to `#F4EEE2` with a dark-scheme variant.

**Acceptance**

- App renders on warm paper, day and night, with no unstyled regions.
- `grep -rn "#[0-9a-fA-F]\{3,6\}" src/styles/components.css` returns nothing.
- `document.documentElement.dataset.theme = "dark"` still flips cleanly.
- All suites green. **No component file has been touched yet.**

This phase alone gets you ~80% of the visual change. Ship it before going on.

---

## Phase 2 — The transcript (~3 h) — the highest-value phase

`play/workspace.tsx`.

1. Add `.is-continuation` to `.gm-message` for beats after the first in a scene.
2. Provisional narration: `--ink-soft` body, `--rule-strong` left border. Pencil
   versus ink. (See [07](07-multiplayer-states.md#provisional-narration).)
3. Build `<Slip>` from the `Check` contract and render `a.resolution.checks` as
   slips — **outside** the `<details>`, not inside it.
4. Add `.transcript-entry--glossed` + `.gloss` for beats whose roll resolves
   inside narration rather than as its own event.
5. Replace `<ArrowUp>` on commit with `<Nib>`; drop `<Sparkles>`.
6. Wrap the composer's channel switch, turn mark and `.acting-as` in
   `.composer-top`; add `.turn-mark` with `<Ring>`.

**Acceptance**

- A committed roll shows dice faces, the effective target, the outcome word and
  the margin **without any disclosure being opened**.
- Outcome maps: `success` → moss "Made it", `failure` → iron "Missed",
  `critical_success` → wax "Critical", `critical_failure` → iron "Critical miss".
- Exactly one drop cap per scene.
- `tests/play.spec.ts` and `src/play/transcript.test.tsx` green.
- Screen reader on a slip announces the roll once, via `<Dice>`'s label.

---

## Phase 3 — Shell and presence (~2 h)

`app.tsx`, `multiplayer/panel.tsx`.

1. Swap lucide `<Compass>` for the ornament in the brand and the nav icon map;
   set `strokeWidth={1.25}` on the remaining lucide icons.
2. `.brand-tagline` becomes the campaign line in the hand.
3. Replace whatever presence display sits in `.topbar-actions` with `.lamp-row`.
4. `.navigation-panel nav a` gets `<i class="nav-tick" />`.
5. `.table-note` gets `<Seal>` and the in-world date.
6. Rebuild `.seat-row` as lamp · name · activity (hand) · status stamp.

**Acceptance**

- No overlapping avatar cluster anywhere.
- Seat states render all eight rows in
  [07](07-multiplayer-states.md#seat-states), each with a word as well as a colour.
- `aside.navigation-panel` and `header.topbar` keep their element types.
- `tests/multiplayer.spec.ts` and `tests/shell.spec.ts` green.

---

## Phase 4 — Character sheet and workshop (~3 h)

`character/pages.tsx`, `character/workshop.tsx`, `character/draft-editor.tsx`.

1. `.stat-grid` → the four-cell ruled block, point costs pencilled underneath.
2. `.resource-pool` → `.track` instead of `<meter>`; `--hurt` at threshold.
3. `.derived-grid` — and **name the modifier** on any derived value that
   includes one (Dodge with `+1 reflexes`, Fright with `+2 reflexes`).
4. `.points-grid` → five cells that reconcile.
5. `.condition-list` → stamps plus the rule that produced each.
6. `.purchase-remove` and `.discard-draft` up to 36px.

**Acceptance**

- The point ledger's fifth cell equals the sum of the first four, for every
  fixture in `character/fixtures.ts` and `character/sample-data.ts`.
- Every derived stat that includes a modifier names it.
- No control below 36px: `document.querySelectorAll('button, a, input, select')`
  and assert `getBoundingClientRect().height >= 36`.
- `.point-budget[data-overspent="true"]` turns iron and says the overage in words.
- `src/character/*.test.tsx` and `tests/inventory.spec.ts` green.

---

## Phase 5 — The remaining screens (~4 h)

Lobby (`setup/lobby.tsx`), inventory (`character/pages.tsx`), tactical
(`play/tactical.tsx`), closure (`adventure/pages.tsx`), voice (`voice/*`).

1. Lobby: tipped-in scenario card with drop cap, seat roster, invite code,
   narration-mode rows, start bar.
2. Inventory: `.custody-note` in the hand; encumbrance as a `.track`.
3. Tactical: map ground `#101824` → `--well` + `--rule-strong`; map marks at the
   ornament stroke weight.
4. Closure: outcome stamps, a fleuron above the advancement block, earned points
   in the hand.
5. Notices: every banner becomes a slip. Remove any toast, if one exists.
6. `.skeleton`: confirm there is no shimmer.

**Acceptance**

- No hex literals outside `tokens.css` anywhere in `src/`.
- Grep for `gradient` returns exactly four hits, all in the design-system CSS:
  `body`, `.play-workspace`, `.gm-message`, `.deckle`. None in any component file.
- Grep for `border-radius` returns only `var(--radius)` and `var(--radius-pill)`.
- `tests/adventure.spec.ts`, `tests/voice.spec.ts`, `tactical-tests/` green.

---

## Phase 6 — Accessibility and responsive pass (~2 h)

1. `node scripts/check-contrast.mjs` — recomputes every calibrated pair from
   `tokens.css` and exits non-zero on any failure. Wire it into `lint:design`.
2. Focus ring: 2px/2px everywhere; inset on the channel switch; traps on
   `.sheet-content` and `.confirm-content`.
3. `<Dice>` carries the roll's accessible name; individual dice hidden.
4. Turn arrival announces once, politely, and does not move focus or scroll.
5. Check 320px, 700px, 1100px and 200% zoom.
6. `prefers-reduced-motion`: waveform and pulse stop, tilt flattens.

**Acceptance**

- Axe or Lighthouse: no new violations against the pre-migration baseline.
- Keyboard-only run of sign-in → lobby → play → commit an action, with a visible
  focus ring at every step.
- At 320px: narration readable, rail is the bottom bar, no horizontal scroll.

---

## Phase 7 — Guard rails (~1 h)

1. Add [`CLAUDE-snippet.md`](CLAUDE-snippet.md) to the repo's `CLAUDE.md`.
2. Add a lint step that fails on the things this system forbids:

```jsonc
// package.json
"lint:design": "node scripts/check-design-system.mjs"
```

The check, at minimum:

- no hex/rgb/hsl literal in `src/**/*.css` outside `tokens.css`
- no hex literal in any `.tsx` `style=` prop
- no `linear-gradient` / `radial-gradient` outside the four allowed selectors
  (`body`, `.play-workspace`, `.gm-message`, `.deckle`), and none at all in `.tsx`
- no `border-radius` value that is not `var(--radius)` / `var(--radius-pill)` / `50%`
- no `letter-spacing` on an uppercase run outside the three label roles
- no emoji in `src/**/*.tsx`
- `node scripts/check-contrast.mjs` exits 0

**Acceptance:** `pnpm lint:design` passes, and fails when you deliberately add a
hex to a component.

---

## Order matters

Phase 1 is safe and reversible and delivers most of the visual change — ship it
on its own. Phase 2 is where the product stops looking like a chat app, so it is
the one to do next even if the rest waits. Phases 3–5 are independent of each
other and can be split across sessions or people. Phase 7 is what stops the
system decaying in three months.

Rough total: **16 hours**, plus phase 0.
