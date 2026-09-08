# 08 — Accessibility

The current app is already careful here — visible labels, `role="status"` on
action state, a skip link, 44px targets, `prefers-reduced-motion` honoured.
This system must not regress any of it. Below is what to preserve, what the
restyle changes, and what to check.

## Contrast — the calibrated pairs

`--ink-faint` is a computed floor, not a taste decision.

| Pair                                            | Ratio | Verdict |
| ----------------------------------------------- | ----- | ------- |
| day `--ink-faint` #6A6252 on `--paper` #F4EEE2  | 5.22  | pass    |
| day `--ink-faint` on `--page` #FBF8F1           | 5.69  | pass    |
| day `--ink-faint` on `--well` #E9E0CD           | 4.60  | pass    |
| day `--ink-faint` on `--wax-soft` #F2E4D8       | 4.84  | pass    |
| day `--ink-soft` #5A5346 on `--paper`           | 6.58  | pass    |
| day `--ink` #201C17 on `--paper`                | 14.66 | pass    |
| day `--wax` on `--paper`                        | 5.73  | pass    |
| day `--moss` on `--paper`                       | 5.28  | pass    |
| day `--amber` on `--paper`                      | 4.64  | pass    |
| day `--iron` on `--paper`                       | 8.74  | pass    |
| day `--page` on `--wax` (primary button)        | 6.24  | pass    |
| night `--ink-faint` #948A79 on `--page` #1E1913 | 5.13  | pass    |
| night `--ink-faint` on `--paper` #16120E        | 5.48  | pass    |
| night `--ink-soft` #B5AA94 on `--page`          | 7.60  | pass    |
| night `--ink` #EDE3D0 on `--paper`              | 14.64 | pass    |
| night `--wax` #D98A5F on `--page`               | 6.44  | pass    |
| night `--moss` #93B76D on `--page`              | 7.67  | pass    |
| night `--amber` #D6AC55 on `--page`             | 8.22  | pass    |
| night `--iron` #E28A80 on `--page`              | 6.80  | pass    |
| night `--page` on `--wax` (primary button)      | 6.44  | pass    |

**If you change any ink or ground token, re-run this table.** The tightest pair
is day `--ink-faint` on `--well` #E9E0CD at 4.60 — that is the one that fails first.

Regenerate with `node scripts/check-contrast.mjs` (shipped in this package); the
ratios above are computed from `tokens.css`, not estimated.

Handwriting is 17–20px, which is large text (≥18.66px at 400 weight), so 3:1
would suffice — but every hand colour in the system clears 4.5:1 anyway. Keep it
that way; Caveat's thin strokes need the extra margin.

## Colour is never the only signal

Every state carries a word:

- Roll outcome — the stamp says "Made it" / "Missed" / "Critical"
- Seat state — the stamp says "Turn" / "Ready" / "Waiting" / "Away" / "Lost"
- Overspent budget — `[data-overspent]` plus "thirteen over" in words
- Pool in danger — the iron edge plus the threshold note

Checkable: no state in this system may be distinguished by a class that changes
only `color`.

## The drawn marks

All ornaments are `aria-hidden` with `focusable={false}`. Meaning lives in the
adjacent text.

`<Dice>` is the exception — it carries the accessible name for the whole roll:

```
role="img" aria-label="3d6: 3, 5, 2, total 10"
```

Individual `<Die>` elements stay hidden. A screen reader announcing "die, die,
die" is worse than silence.

## Live regions

- `.action-status` is already `role="status"`. Keep it. Never animate it, never
  move it in the DOM, and keep its text short — it is read on every change.
- Turn arrival gets one polite announcement, not a focus move.
- Do not add a second live region to the transcript. Streamed narration
  announced token by token is unusable.

## Focus

One ring: `2px var(--focus)` at `2px` offset. It replaces the current `3px` at
`4px` offset — thinner and tighter, but `--focus` is a strong blue against warm
paper and it stays clearly visible.

- The channel switch uses an **inset** ring (`outline-offset: -2px`) because the
  segmented control clips overflow.
- `.sheet-content` and `.confirm-content` need a focus trap, Escape to close, and
  focus returned to the trigger.
- Never remove an outline without replacing it.

## Reach

44px on actions and navigation; 36px floor in dense panels. Nothing below 36.
The current stylesheet has several 32px controls (`.discard-draft`,
`.purchase-remove`, `.lobby-account .button`); all move up.

## Motion

Two animations exist: `voice-wave` and `voice-pulse`, both only while the mic is
held. Both stop under `prefers-reduced-motion`.

That media query also flattens tilt on slips, stamps, glosses and asides. Tilt is
static, not motion, so this is a judgement call rather than a requirement — but a
reader who has asked for calm gets a flat page, and nothing is lost because tilt
carries no information.

**No shimmer on `.skeleton`.** A pulsing placeholder next to prose is exactly
the kind of movement this product should not have.

## Reading

- Narration is 19px on 34px leading at a 60ch measure — comfortably above every
  readability floor, and that is the point of the whole system.
- `white-space: pre-wrap` on narration and player text: the author's paragraph
  breaks are content.
- `overflow-wrap: anywhere` on headings and prose, kept from the current sheet —
  character names and campaign titles are user-supplied and can be long.

## Zoom and reflow

Everything must survive 200% zoom and a 320px viewport. The specific risks:

- `.stat-grid` at four columns — goes to two below 700px
- `.points-grid` at five — goes to two
- `.transcript-entry--glossed` — collapses below 1100px
- The margin rule — removed below 700px

## Semantics to preserve

`aside.navigation-panel`, `header.topbar`, `main`, `<li class="transcript-entry">`
inside a list, `<time datetime>` on timestamps, `#page-title` and
`#inventory-title` (used for labelling). The Playwright suites assert on several
of these; see [09](09-migration-map.md#what-the-tests-select-on).
