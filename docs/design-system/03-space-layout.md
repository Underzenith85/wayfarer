# 03 — Space and layout

## Scale

4px base: `--s-1` 4, `--s-2` 8, `--s-3` 12, `--s-4` 16, `--s-5` 22, `--s-6` 34,
`--s-7` 48, `--s-8` 64. `--s-5` and `--s-6` break the doubling deliberately —
22 is the transcript's gap and 34 is the ruling pitch, and both need to be
expressible in the scale so they do not get hard-coded.

## Measure

| Token                 | Value  | Applies to                                                 |
| --------------------- | ------ | ---------------------------------------------------------- |
| `--measure-narration` | `60ch` | `.gm-message`                                              |
| `--measure-voice`     | `54ch` | `.player-message`                                          |
| `--measure-panel`     | `56ch` | prose inside cards, `.empty-region`, `.unavailable-region` |

Asides are 48ch, set directly — they are short by nature and a token for one
use is noise.

## The shell

```
┌─ .topbar ────────────────────────────────────────────── 74px ─┐
│ compass  WAYFARER │ campaign (hand)        lamps  buttons     │
├───────────┬──────────────────────────────────┬────────────────┤
│ .naviga-  │ .play-workspace  (the leaf)      │ .character-    │
│ tion-     │                                  │ panel          │
│ panel     │   ┆ 64px                          │                │
│           │   ┆← wax margin rule              │  seats         │
│  220px    │   ┆                               │  pools         │
│           │   ┆  scene head                   │  conditions    │
│           │   ┆  transcript                   │  pinned sketch │
│           │   ┆  composer (margin-top:auto)   │                │
│           │                                   │   328px        │
└───────────┴───────────────────────────────────┴────────────────┘
   --rail-width      minmax(0,1fr)                --party-width
```

`--shell-width: 1440px`, centred. The old `--shell-column: 1600px` is retired:
at 1600 the narration measure is unreachable without the leaf growing dead space
on both sides, and every screen in the design is drawn at 1440.

### The margin rule

The vertical wax hairline 64px from the leaf's left edge is painted as a
`linear-gradient` background on `.play-workspace`, not as a border on a child.
That way it runs the full height of the leaf regardless of what content is in
it, exactly like a ruled page. `.leaf-inner` holds content clear of it with
`padding-left: 88px`.

`--margin-rule-x` drops to `0px` at ≤700px and the background is removed — on a
phone the leaf has no margin to rule.

### The composer sits at the bottom

`.composer { margin-top: auto }` inside a flex column. This requires the column
to have a **definite height** — `.workspace` gets it from
`min-height: calc(100dvh - var(--topbar-height))`. If a wrapper between them
loses its height, `margin-top:auto` silently stops working and the composer
floats up under the transcript. That is the failure mode to look for if the
composer ever looks wrong.

## Reach

- `--reach: 44px` — actions, navigation items, form controls
- `--reach-dense: 36px` — the floor inside dense panels (`.button-sm`,
  `.discard-draft`, `.purchase-remove`, summary toggles)

36 is a real floor, not an aspiration: nothing goes below it. The 32px controls
in the current stylesheet all move up.

## Edge

- `--radius: 2px` — everything. Paper does not have 12px rounded corners.
- `--radius-pill: 999px` — the lamp glow only. Never a control, never a chip.
- Borders are `1px var(--rule)`; emphatic ones `1px var(--rule-strong)`.
- Focus is `2px var(--focus)` at `2px` offset. One value, everywhere.

## Tilt

| Token          | Value  | Applies to                                  |
| -------------- | ------ | ------------------------------------------- |
| `--tilt-slip`  | `0.4°` | `.player-message`, `.slip`                  |
| `--tilt-hand`  | `1.6°` | `.gloss`, `.hand`, `.aside`, captions       |
| `--tilt-stamp` | `4.5°` | `.stamp`, `.outcome`                        |
| —              | `8°`   | dice, in the fixed table in `Ornaments.tsx` |

Tilt is deterministic, never random. The same roll must render identically every
time it is drawn.

## Breakpoints

Two, matching the app's existing ones.

**≤1100px** — the party panel is hidden and reachable through `.sheet-content`
instead; the gloss column collapses and the gloss moves _under_ the narration,
never into it.

**≤700px** — the leaf loses its margin rule and left padding; the rail becomes a
five-item bottom bar; narration drops to 17px/30px (ruling pitch follows);
attribute and points grids go to two columns.

The narration ruling, the margin rule and the gloss column are desktop
affordances. On a phone the journal is a single ruled column, which is the right
answer — a 390px page does not have margins to write in.
