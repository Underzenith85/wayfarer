# 09 — Migration map

All **172** class selectors in `frontend/src/styles.css` are accounted for in
the new stylesheet. Nothing is left unstyled after the swap, and **no class is
renamed** — the whole migration is a stylesheet replacement plus a small number
of markup additions.

That is deliberate: the test suites select on several of these class names.

---

## What the tests select on

Across `tests/`, `live-tests/`, `pwa-tests/`, `startup-tests/`,
`tactical-tests/` and the Vitest component tests there are **503**
`getByRole`, **146** `getByText` and **186** `getByLabel*` calls, versus **57**
`locator()` calls. The suites are behaviour-based, so a restyle is low-risk.

The class and element selectors that _are_ asserted on:

```
.transcript-entry   .multiplayer-panel   .item-card      .inventory-totals
.purchase-cost      .point-budget        .presence-list  .scene-description
.inventory-page     .context-actions     .character-draft-editor
.attribute-row      header.topbar        aside.navigation-panel
main   body   time   summary   details   form   select   label
#page-title   #inventory-title
```

**Every one survives.** The element types matter too: `.topbar` must stay a
`<header>`, `.navigation-panel` an `<aside>`, `.transcript-entry` an `<li>`.

There are no screenshot or snapshot baselines, so nothing needs re-recording.

---

## Token renames

The only breaking renames are custom properties. `styles.css` currently declares
nine; here is where each goes.

| Old                       | New                               | Note                                                          |
| ------------------------- | --------------------------------- | ------------------------------------------------------------- |
| `--background` `#f3f5f7`  | `--paper`                         | and it is now the page ground, not an unused variable         |
| `--surface` `#fff`        | `--page`                          |                                                               |
| `--foreground` `#192431`  | `--ink`                           |                                                               |
| `--muted` `#526171`       | `--ink-soft` **or** `--ink-faint` | two roles were sharing one token; see below                   |
| `--border` `#cbd3db`      | `--rule`                          | `--rule-strong` and `--rule-faint` are new                    |
| `--accent` `#94451d`      | `--wax`                           | the closest thing to a direct carry-over — your rust survives |
| `--accent-soft` `#f8e9df` | `--wax-soft`                      |                                                               |
| `--danger` `#9a1c24`      | `--iron`                          |                                                               |
| `--danger-soft` `#fbe7e8` | `--iron-soft`                     |                                                               |
| `--focus` `#075dc8`       | `--focus`                         | unchanged name, new value                                     |
| `--shell-column` `1600px` | `--shell-width` `1440px`          | see [03](03-space-layout.md#the-shell)                        |
| `--shell-gutter` `2rem`   | —                                 | replaced by the rail/leaf/party grid                          |
| —                         | `--moss`, `--amber`               | **new**: success and partial had no token at all              |

### `--muted` was doing two jobs

It is secondary prose in some places and 12px meta in others. Split it:

- prose, descriptions, `<p>` inside panels → `--ink-soft`
- labels, timestamps, hints, counts, `.meta` → `--ink-faint`

When in doubt: if it is a sentence, `--ink-soft`; if it is a label, `--ink-faint`.

### Hard-coded colours to remove

`styles.css` contains hexes outside `:root` that must become tokens:

| Location                                | Current                                  | Replace with                                            |
| --------------------------------------- | ---------------------------------------- | ------------------------------------------------------- |
| `.outcome-success`, `.outcome-achieved` | `#277044` on `#e8f5ec`                   | `--moss` on `--moss-soft`                               |
| `.outcome-partial`                      | `#8a5200` on `#fff3d7`                   | `--amber` on `--amber-soft`                             |
| `.outcome-failure`, `.outcome-failed`   | `#9a3131` on `#fdeaea`                   | `--iron` on `--iron-soft`                               |
| dark-mode variants of the above         | six more hexes                           | the same four tokens — night is handled by `tokens.css` |
| `.tactical-map`                         | `background:#101824`                     | `--well` + `--rule-strong` border                       |
| `.point-budget[data-overspent]`         | `#ffb4b4` on `#4a171f`                   | `--iron` on `--iron-soft`                               |
| `.generation-failure`                   | `#ffdede` on `#4a171f`, border `#ffb4b4` | `--iron` family                                         |
| `.purchase-remove:hover`                | `#ffb4b4`                                | `--iron`                                                |
| `.concept-conflict`                     | `var(--accent,#7aa2f7)`                  | `--wax` (drop the fallback)                             |
| `.sheet-overlay`                        | `#0009`                                  | `rgba(22,18,14,.62)`                                    |
| `.point-budget`                         | `var(--foreground,#f3f5f7)`              | `--ink` (drop the fallback)                             |
| `.draft-controls details`               | `var(--border,#536074)`                  | `--rule` (drop the fallback)                            |

The `var(--x, #fallback)` patterns are worth removing on their own: three of
them fall back to blues from a palette that no longer exists, so if the variable
ever fails to resolve the page turns a different colour scheme rather than
failing visibly.

---

## Class inventory — all 172

**Kept and restyled, no markup change (147).** Everything in `styles.css` not
listed in the two sections below. Most of the work is here and it is CSS-only.

**Kept, restyled, needs a small markup addition (9):**

| Class                         | Addition                                                                             |
| ----------------------------- | ------------------------------------------------------------------------------------ |
| `.gm-message`                 | add `.is-continuation` on beats after the first in a scene, to suppress the drop cap |
| `.navigation-panel nav a`     | add `<i class="nav-tick" />` before the label                                        |
| `.topbar-actions`             | replace the presence display with `.lamp-row`                                        |
| `.committed-result`           | render `resolution.checks` as `.slip` rows, out of the `<details>`                   |
| `.transcript-entry`           | add `.transcript-entry--glossed` when a beat has an inline roll                      |
| `.composer-top` (new wrapper) | wrap the channel switch, turn mark and `.acting-as`                                  |
| `.resource-pool`              | add `.resource-pool--hurt` when at or below the threshold                            |
| `.seat-row`                   | add `.seat-lamp`, `.seat-name` wrappers and a `<Lamp>`                               |
| `.table-note`                 | add `<Seal>`                                                                         |

**New classes (24).** No existing markup breaks by adding these:

`.slip` `.slip--fail` `.slip--crit` `.slip-what` `.slip-verdict` `.dice`
`.gloss` `.transcript-entry--glossed` `.stamp` (+ 5 modifiers) `.scene-break`
`.turn-mark` `.lamp-row` `.nav-tick` `.leaf-inner` `.seat-lamp` `.seat-name`
`.resource-pool--hurt` `.derived-grid` `.derived-cell` `.track` `.is-taped`
`.deckle` `.pinned-sketch` `.pinned-caption` `.card--tipped` `.button-ghost`
`.button-sm` `.label` `.tag` `.field`

**Deprecated but still styled (1):** `.committed-result` keeps its rule so the
current markup does not break mid-migration. Remove it once every call site
renders `.slip`.

---

## Markup corrections found by reading the source

Two things in the design canvas did not match the real DOM, and the shipped CSS
in this package is the corrected version:

1. **`.channel-ooc` is a modifier, not an element.** `workspace.tsx:312` renders
   `<div className={"player-message channel-" + entry.channel}>`, so the aside
   treatment has to _undo_ the slip treatment (`background: none; border: 0;
box-shadow: none`) rather than sit alongside it.

2. **`.who` is already a `<span class="eyebrow">`.** Both `.gm-message` and
   `.player-message` render their attribution that way, which means the drop cap
   selector `p:first-of-type::first-letter` works unmodified, and the eyebrow
   just gets restyled into the hand inside `.gm-message`. No rename needed — but
   if that `<span>` ever becomes a `<p>`, the drop cap lands on the wrong line.

Also: slip tilt alternates via `.transcript-entry:nth-of-type(even)`, not
`.player-message:nth-of-type(even)` — each message is alone inside its own `<li>`.

---

## Component-layer changes

### `components/ui/button.tsx`

The CVA already maps variants onto exactly these class names. Add two:

```tsx
const variants = cva("button", {
  variants: {
    variant: {
      default: "button-primary",
      outline: "button-outline",
      ghost: "button-ghost", // NEW
      danger: "button-danger",
    },
    size: { default: "", sm: "button-sm" }, // NEW
  },
  defaultVariants: { variant: "default", size: "default" },
});
```

### lucide-react

Seven files import lucide icons. Lucide's default `strokeWidth` is `2` on a 24px
grid; the drawn ornaments are `1.1–1.2`. Side by side the lucide icons look
bold and out of voice. Set `strokeWidth={1.25}` at every call site, or wrap once:

```tsx
export const Icon = (I: LucideIcon) => (p: LucideProps) => (
  <I strokeWidth={1.25} absoluteStrokeWidth {...p} />
);
```

Three lucide icons are replaced by ornaments outright:

| Was                   | Becomes                 | Where                                                                                                                                  |
| --------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `<Compass>` (lucide)  | `<Compass>` (Ornaments) | `app.tsx` brand and nav icon map                                                                                                       |
| `<Sparkles>`          | remove                  | `workspace.tsx`. A sparkle on an AI affordance is the single most generic signifier available; the GM's voice already says what it is. |
| `<ArrowUp>` on commit | `<Nib>`                 | `workspace.tsx`                                                                                                                        |

Keep lucide for utility affordances: `X`, `Trash2`, `Mic`, `Square`, `Volume2`,
`VolumeX`, `ChevronRight`, `Menu`, `PanelRight`, `SunMoon`.

### `index.html`

`<meta name="theme-color" content="#18232e">` is the old dark surface. It should
be `#F4EEE2`, with a `media="(prefers-color-scheme: dark)"` variant at `#16120E`.
The PWA manifest icons are also from the old palette and will look wrong beside
a warm-paper app.

### Tailwind

Tailwind v4 is installed via `@tailwindcss/vite` and `styles.css` opens with
`@import "tailwindcss"`. In the shipped bundle it contributes preflight plus
about twelve utilities (`.visible .fixed .relative .static .isolate .container
.block .hidden .table .outline .blur .filter`), and **zero utility classes appear
in the markup**. `components.json` points shadcn at it with `baseColor: slate`,
which is not this palette.

Decide before starting. See [10 — Implementation plan](10-implementation-plan.md),
phase 0.
