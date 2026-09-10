# 05 — Components

Anatomy, states and rules. Classes marked **NEW** do not exist in the current
stylesheet; everything else keeps its name, so most of this is a restyle.
The full old→new table is in [09 — Migration map](09-migration-map.md).

---

## Button — `.button`

Square-cut, 44px, tracked caps, no gloss.

| Variant                 | Recipe                            | Use                                                                              |
| ----------------------- | --------------------------------- | -------------------------------------------------------------------------------- |
| `.button-primary`       | wax fill, `--page` text           | one per screen. The thing you came to do.                                        |
| `.button-outline`       | page fill, `--rule-strong` border | everything else                                                                  |
| `.button-ghost` **NEW** | no border, `--ink-faint`          | tertiary; "keep the draft"                                                       |
| `.button-danger`        | transparent, iron border and text | destructive. Never filled — a filled destructive button is a misclick generator. |
| `.button-sm` **NEW**    | 36px, 10.5px                      | dense panels only                                                                |

**States.** hover `brightness(1.06)`; outline hover also takes a wax border.
`:disabled` → `opacity .42`, `cursor: not-allowed`, no filter. Focus is the
global ring; do not add a per-button one.

**Rules.** No inset highlight. No icon-only buttons without a `.visually-hidden`
label. The commit action carries `<Nib>`; no other button carries a mark.

---

## Tabs — `.mode-tabs` / `.mode-tab`

Underscored in wax when `aria-selected="true"`, never filled. A filled tab
competes with the primary button two inches away.

Needs `role="tablist"` / `role="tab"` / `aria-selected`, and arrow-key roving
focus. The current markup already uses `aria-selected`; keep it.

---

## Fields

Inputs are **wells**: `--well` ground, `--rule-strong` border, 44px min height.
A form reads as blanks left on a sheet.

- Labels are `.label` (11px caps, `.14em`), above the field, always visible.
  No placeholder-as-label.
- `textarea` is serif 16/1.7 — the player is writing prose, so it should look
  like prose while they type.
- `input[type=number]` is monospace with tabular figures.
- Placeholder is `--ink-faint`, which clears 4.5:1 on `--well` in both themes.
  Do not lighten it.

---

## Channel switch — `.channel-switch`

Segmented control: **Action / Dialogue / Aside**. One of only two places a wax
fill is spent, because the choice changes how everything typed will be read.

Radio inputs visually hidden inside `<label>`; the checked state is styled with
`:has(input:checked)`. Focus is handled by `:has(input:focus-visible)` with an
inset ring, since an outset ring would be clipped by `overflow: hidden`.

**Do not** convert this to buttons with `aria-pressed`. It is a single-choice
group and radios give keyboard users arrow-key selection for free.

---

## The turn mark — `.turn-mark` **NEW**

"your turn" in the hand, with a hand-drawn `<Ring>` around it. Once per screen,
in the composer. It is decoration on top of information that already exists in
the seat list — a colour-blind or reduced-motion user loses nothing if it fails
to render.

---

## Transcript voices

### GM narration — `.gm-message`

Ruled ground, 2px wax left border, `.who` line in the hand, first paragraph
opens on a wax drop cap.

- Ruling pitch and line-height are the same token (see
  [02](02-typography.md#narration-leading-is-in-px-on-purpose)).
- `.who` must be a `<span>`, not a `<p>`, or the drop cap lands on it.
- Add `.is-continuation` for beats after the first in a scene — one drop cap per
  scene.
- `white-space: pre-wrap`; the GM's paragraph breaks are content.

### Player action / dialogue — `.player-message`

A slip laid on the page: page ground, hairline border, `--lift`, ±0.4° tilt
alternating by `:nth-of-type(even)`. `.channel-dialogue` adds a 2px
`--rule-strong` left edge. Timestamp in the hand.

### Aside — `.channel-ooc`

The margin: dashed left rule, **entirely in the hand** at 20px, `--ink-soft`,
−0.7° tilt. No card, no fill. It is a note in the margin, not a message.

### Scene break — `.scene-break` **NEW**

`<Fleuron>` — text — `<Fleuron>`. Replaces the horizontal rule between beats.
The text is in the hand: "Sister Ansa held her turn".

---

## The roll slip — `.slip` **NEW** (replaces `.committed-result`)

The signature component. A GURPS roll is 3d6 against an effective skill, so:

```
[ dice ]   BROADSWORD                         [MADE IT]
           skill 14, −2 in the dark = 12      rolled 10, by 2
```

Three columns: `<Dice>`, `.slip-what`, `.slip-verdict`.

| Element                 | Voice     | Why                            |
| ----------------------- | --------- | ------------------------------ |
| the skill name          | sans caps | a system label                 |
| the modifier arithmetic | **hand**  | the player's working           |
| the verdict             | **stamp** | the system's ruling            |
| the margin              | **hand**  | the number players argue about |

Variants: default (moss edge), `.slip--fail` (iron), `.slip--crit` (wax edge and
`--wax-soft` ground). The verdict word changes with the variant — "Made it",
"Missed", "Critical" — and is never omitted.

**Always show the margin.** In GURPS, margin of success drives more rules than
the pass/fail bit does; a slip without it is missing the useful half.

---

## Marginal gloss — `.gloss` **NEW**

The same information as a slip, in the right margin, when a roll resolves inside
narration rather than as its own event. Parent is
`.transcript-entry--glossed` (grid, `1fr 156px`).

Below 1100px the gloss moves under the narration. It never moves into it, and it
never becomes a modal.

---

## Stamps — `.stamp`, `.outcome`

Word first, pigment second. `.stamp--moss` / `--amber` / `--iron` / `--wax` /
`--quiet`, plus `.stamp--sm` for dense panels. Existing `.outcome-success`,
`.outcome-partial`, `.outcome-failure`, `.outcome-achieved`, `.outcome-failed`
all map onto the same recipe.

One per element. Never the only signal for a state.

---

## Attributes and pools

### `.stat-grid` / `.attr-block`

Four cells in one ruled block: ST, DX, IQ, HT. Value is 32px monospace; the
point cost beneath is **pencilled** (`.attr .d`, hand, wax) because it is the
player's accounting rather than a system assertion.

### `.resource-pool`

Current over maximum, plus a `.track`. `--iron` top edge and iron value **only**
when the pool is in danger (`.resource-pool--hurt`). The threshold note beneath
("reeling below 4") is in the hand.

`.track` replaces `<meter>`, which cannot be styled consistently across engines.
`<i class="is-iron">` sets the fill colour.

### `.derived-grid` **NEW** / `.derived-preview dl`

Will, Per, Basic Speed, Basic Move, Dodge, Parry, Block, Encumbrance. Where a
derived value includes a modifier, name it in the hand underneath: "+1 reflexes".
This matters — a player looking at Dodge 9 needs to know the 9 already includes
Combat Reflexes, or they will add it twice.

### `.points-grid`

Attributes / Advantages / Disadvantages / Skills / Spent-of-total. Five cells.
It must actually add up; see [06](06-screens.md#character-sheet).

---

## The ledger — `.sheet-stats`, `.ledger`

One table style for skills, advantages, disadvantages, equipment. Right-aligned
monospace values, `.m` for the relative level (`DX+2`), `td small` for the rules
note under a trait. Hairline rows, **no zebra striping** — the rules do that work.

---

## Seats — `.seat-row`

`<Lamp lit>` · name · what they are doing (hand) · status stamp.

| State        | Lamp     | Stamp           | Line                    |
| ------------ | -------- | --------------- | ----------------------- |
| acting       | lit, wax | amber "Turn"    | "Marisol, writing"      |
| ready        | unlit    | moss "Ready"    | "Dev, spoke at 21:13"   |
| waiting      | unlit    | quiet "Waiting" | "Priya, held"           |
| away         | unlit    | quiet "Away"    | "Sam, away six minutes" |
| disconnected | unlit    | iron "Lost"     | "reconnecting…"         |

Presence never becomes a toast, a badge count or a red dot. See
[07 — Multiplayer states](07-multiplayer-states.md).

---

## Notices — `.availability-banner`, `.offline-banner`, `.request-error`

A slip laid on the page: pigment left edge, `-soft` ground, `--lift`, −0.3° tilt.
Amber for waiting, iron for failure, `--well` + faint edge for offline.

**There are no toasts in this system.** A message worth showing is worth leaving
on the page until it stops being true.

---

## Point budget — `.point-budget`

Sticky, visible through the whole character workshop. Monospace total, `.label`
caption, `.track`, and the remainder in the hand ("six in hand"). Turns iron via
`[data-overspent="true"]`, which the app already sets.

---

## Overlays — `.sheet-overlay`, `.sheet-content`, `.confirm-content`

The party panel below 1100px, and destructive confirmations. Needs a focus trap,
`Escape` to close, and focus returned to the trigger. The current CSS has the
surfaces; the behaviour is the implementer's job.
