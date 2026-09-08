# 04 — Surfaces and ornament

## Four surfaces

| Surface       | Recipe                                                 | Means                                   |
| ------------- | ------------------------------------------------------ | --------------------------------------- |
| **Page**      | `--page` + 1px `--rule` + `--shadow`                   | a sheet lying on the desk               |
| **Well**      | `--well` + 1px `--rule-strong`, no shadow              | recessed: inputs, tracks, the draft box |
| **Tipped-in** | Page + 3px `--wax` left edge + `--lift`                | a sheet pasted onto the page            |
| **Ruled**     | Page + `repeating-linear-gradient` at `--ruling-pitch` | narration, and only narration           |

Two elevations, and there is no third. `--shadow` is a sheet on the desk;
`--lift` is a sheet on top of another sheet. Anything that needs to feel higher
than `--lift` is a modal, and modals get `.sheet-overlay`.

### The tipped-in edge means one thing

A 3px left wax edge means _this sheet is pasted on_. It is not a generic accent
bar. Currently: `.scene-card`, `.clarification`, `.card--tipped`. Notices use
their own pigment's 3px edge (amber, iron) for the same reason — a notice is
also a slip laid on the page.

### Ruling is only ever behind narration

Ruling a card, a panel or a form turns the product into stationery. The one
exception that would be legitimate is a future "journal export" view; nothing
else.

---

## Ornament that is earned

Every drawn mark has one meaning and one home. The full table is in
[`ornaments/README.md`](../ornaments/README.md). The rules:

1. **Stroked SVG**, 16 or 24px grid, `stroke-width` 1.1–1.2. Never emoji.
2. **`currentColor`.** `<Seal>` is the only exception — wax is genuinely filled.
3. **`aria-hidden`.** Meaning lives in adjacent text. `<Dice>` is the exception:
   it carries the accessible name for the whole roll.
4. **One meaning each.** If a mark needs a second, draw a new mark.

### Tape

`.is-taped` puts a strip of gummed paper across a card's top edge. At most one
taped card per screen. It means "someone stuck this here", not "this is
important" — for importance, use the tipped-in edge.

### Deckle

`.deckle` is a torn top edge. Currently unused in the shipped screens; it exists
for a future printed-handout or scenario-export view. Do not scatter it.

### Pinned sketches

`.pinned-sketch` + `.pinned-caption` in the party margin. These are a _player's_
drawings. They are captioned in the hand, and — this is the important part —
**they never carry information the system needs to assert.** A sketch may be
wrong. A player may have drawn the door on the wrong side. That is the point,
and it is also why nothing may depend on one.

### Stamps

`.stamp` is a double-ruled, letterspaced, rotated, slightly faded rubber stamp:
`border: 2px solid currentColor` plus `box-shadow: inset 0 0 0 1px currentColor`
gives the double rule, `opacity: .84` gives the ink fade.

`.stamp--sm` is the dense variant for seat status and condition chips.

One stamp per element. A row of four stamps at four angles reads as clutter; the
angles are per-pigment and fixed (moss −3°, amber +2.5°, iron −2°) so a row of
mixed states still looks scattered rather than combed, without being noisy.

---

## The things this system does not have

Listed because each one is a thing a well-meaning change will reintroduce:

- Gradients for depth, glow, sheen or a background wash (the four structural
  ones in [01](01-color.md#texture) and [03](03-space-layout.md#the-margin-rule) draw paper features, and there is no fifth)
- Glassmorphism, backdrop blur, translucent panels
- Rounded corners above 2px
- Overlapping avatar stacks
- Inset highlights on filled buttons
- Toasts
- Red notification dots and badge counts
- Icon-only buttons without a label
- Emoji, in components or in copy
- A third elevation
- A fourth label role
- Any hex value outside `tokens.css`
