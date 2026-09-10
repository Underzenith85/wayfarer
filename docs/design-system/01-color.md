# 01 — Colour

Every colour in the product is a token in `css/tokens.css`. No component file,
no inline style and no component prop may contain a hex value. If you need a
colour that is not here, the answer is almost always an existing token used
differently — and if it genuinely is not, add it to `tokens.css` in both themes
and document it here in the same commit.

## The ground — three steps, in one direction

| Token     | Day       | Night     | What sits on it                                       |
| --------- | --------- | --------- | ----------------------------------------------------- |
| `--paper` | `#F4EEE2` | `#16120E` | The page itself. Carries the fibre texture.           |
| `--page`  | `#FBF8F1` | `#1E1913` | One step **toward** the light: cards, panels, topbar. |
| `--well`  | `#E9E0CD` | `#110E0B` | One step **away**: inputs, tracks, the draft box.     |

Note that night is not an inversion. `--well` goes _darker_ than `--paper` in
both themes, because a well is recessed in both. Inverting it — making the input
lighter than the page at night — is the single most common way this palette gets
broken.

## The ink — three weights

| Token         | Day       | Night     | Use                             |
| ------------- | --------- | --------- | ------------------------------- |
| `--ink`       | `#201C17` | `#EDE3D0` | Body text, headings, values     |
| `--ink-soft`  | `#5A5346` | `#B5AA94` | Descriptions, secondary prose   |
| `--ink-faint` | `#6A6252` | `#948A79` | Labels, meta, timestamps, hints |

`--ink-faint` is the floor and it is calibrated, not chosen by eye. Day
`#6A6252` clears 4.5:1 on `--paper` (4.59), `--page` (4.75) and `--well` (4.65).
Night `#948A79` clears it on `--page` (5.17). **Lighten day `--ink-faint` or
darken night `--ink-faint` and something fails** — re-run the check in
[08 — Accessibility](08-accessibility.md) if you touch either.

Night text is `#EDE3D0`, never `#FFFFFF`. Pure white on a warm dark ground reads
as a different material and breaks the lamplight.

## The rules — three weights

| Token           | Day       | Night     | Use                                             |
| --------------- | --------- | --------- | ----------------------------------------------- |
| `--rule-faint`  | `#E4DAC4` | `#2A231A` | **Only** the horizontal ruling behind narration |
| `--rule`        | `#D8CDB5` | `#362E23` | Hairline dividers, card borders                 |
| `--rule-strong` | `#B9A985` | `#524736` | Table heads, emphatic rules, input borders      |

## The four pigments

Each is a colour a scribe would have had on the desk. That is what keeps the set
coherent as it grows — when you need a fifth state, ask which of these four it
actually is before reaching for a new hue.

| Pigment   | Day       | Night     | Means               | May mark                                                                                                                                     |
| --------- | --------- | --------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `--wax`   | `#8F4A24` | `#D98A5F` | **this**, the brand | Active nav, the primary action, the margin rule, the campaign seal, the channel switch fill, the drop cap, the gloss text. **Nothing else.** |
| `--moss`  | `#4A6B33` | `#93B76D` | success             | Made rolls, met objectives, a ready seat                                                                                                     |
| `--amber` | `#8A6414` | `#D6AC55` | partial / waiting   | Partial success, whose turn it is, a character still being built, the GM composing                                                           |
| `--iron`  | `#7B2028` | `#E28A80` | failure / danger    | Failed rolls, criticals against, a pool in danger, destructive actions                                                                       |

Each has a `-soft` companion for backgrounds (`--wax-soft`, `--moss-soft`,
`--amber-soft`, `--iron-soft`).

### Wax is rationed

The list above is exhaustive. The most common drift in this system is wax
appearing on every stat panel, every card edge and every heading — at which
point it stops meaning "this" and starts meaning nothing. Concretely:

- `.resource-pool` gets a coloured top edge **only** when the pool is in danger,
  and then it is `--iron`, not wax.
- A 3px left wax edge means exactly one thing: **this sheet is tipped in**
  (`.card--tipped`, `.scene-card`, `.clarification`). Do not use it as a generic
  accent bar.
- Section headings are `--ink-faint`. `.eyebrow-wax` exists for the one heading
  per screen that identifies the current thing.

### Colour never carries meaning alone

Every stamp, pill and status carries a word. A player who cannot distinguish
moss from iron must still be able to read "Made it" and "Missed". This is not
optional and it is checkable: grep for any element whose only differentiator
between states is a class that changes `color`.

## Focus

`--focus` (`#2C4A7A` / `#8FBBEE`) is the one hue in the system that is not a
pigment, and it is used for exactly one thing: `:focus-visible`, 2px, 2px
offset. It never appears as a text colour, a border, or a fill.

## Texture

`--stain-a` and `--stain-b` are foxing; `--fiber` and `--fiber-cross` are paper
fibre. Together they are the only gradient in the product, painted on `body` in
`base.css` with `background-attachment: fixed` so the grain stays with the page
rather than scrolling with content.

The body ground is one of **four** structural gradients in the system — the
others draw the wax margin rule (`.play-workspace`), the ruling behind narration
(`.gm-message`) and the torn edge (`.deckle`). Every one of them draws a physical
feature of paper. No gradient may be used for depth, glow or sheen.

They are deliberately near-invisible (4–5% alpha). If you find yourself raising
the alpha to "make the texture show", the fix is the opposite: the texture is
meant to be felt, not seen, and a visible mottle reads as a JPEG artefact.

`--tape` / `--tape-edge` are gummed paper tape, used by `.is-taped`. At most one
taped card per screen.
