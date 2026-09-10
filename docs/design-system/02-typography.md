# 02 — Typography, and the hand rule

Four families. They never trade jobs.

| Voice | Family            | Fallbacks                  | Speaks for  |
| ----- | ----------------- | -------------------------- | ----------- |
| serif | **Spectral**      | Georgia, Iowan Old Style   | the world   |
| sans  | **Archivo**       | Segoe UI, system-ui        | the machine |
| mono  | **IBM Plex Mono** | Cascadia Mono, Consolas    | the numbers |
| hand  | **Caveat**        | Segoe Script, Bradley Hand | the people  |

Fallbacks are chosen to be metric-close on Windows and macOS, because **PDF and
PNG export never embed webfonts** — an exported character sheet renders in the
fallback stack, so the layout must survive it.

Self-host all four in production. The `@import` in `base.css` is for getting
started; it costs a render-blocking round trip to `fonts.googleapis.com` and it
sends your users' IPs to Google.

---

## The hand rule

This is the load-bearing rule of the whole system. Handwriting is the difference
between a game and a ticketing system, and it is also the fastest route to
something unreadable. So:

> **The hand is for what a _person_ wrote.
> Everything the system asserts stays set in type.**

### Written in the hand

- The margin gloss on a roll — it is the player's own arithmetic
- Asides and out-of-character notes (`.channel-ooc`)
- What a player is doing right now, under their character's name
  (`.seat-character`: "Marisol, writing")
- Captions on a pinned sketch (`.pinned-caption`)
- The campaign line in the topbar (`.brand-tagline`) and in-world dates
- A player's note to themselves on their own sheet
- Point costs pencilled under an attribute (`.attr .d`) — the player's accounting
- Hints and draft state in the composer (`.composer-hint`, `.composer-count`)
- Empty states (`.empty-region`) — "nothing here yet" is a note, not an error

### Never in the hand

- **Any number that has to be compared to another number.** Attributes, pools,
  point totals, costs, skill levels, dice totals. These are monospace with
  `font-variant-numeric: tabular-nums`, always.
- Anything the system asserts: errors, availability, save state, validation
- Buttons, navigation, table heads, form labels
- Anything below 16px — Caveat's x-height makes small sizes illegible
- Anywhere it would carry meaning alone

The two live side by side in the gloss and that is deliberate: `13 −2 dark = 11,
rolled 9, by 2` is the player's working, in their hand; `MADE IT` beside it is
the system's verdict, stamped.

---

## The ramp

| Role           | Family    | Size / line      | Weight  | Notes                                       |
| -------------- | --------- | ---------------- | ------- | ------------------------------------------- |
| `display`      | Spectral  | 52 / 1.0         | 400     | Masthead only                               |
| `scene-title`  | Spectral  | 31–33 / 1.15–1.2 | 500     | Scene, character and campaign headings      |
| `narration`    | Spectral  | 19 / **34px**    | 400     | 60ch. The largest body on screen            |
| `player-voice` | Spectral  | 16 / 1.7         | 400     | Declared actions and dialogue, 54ch         |
| `body`         | Archivo   | 15 / 1.55        | 400     | Panels, forms, descriptions                 |
| `meta`         | Archivo   | 12 / 1.5         | 400     | Counts, dates, technical detail             |
| `.eyebrow`     | Archivo   | 10.5 caps        | 700     | Section and panel headings · `.19em`        |
| `.label`       | Archivo   | 11 caps          | 700     | Field and stat labels · `.14em`             |
| `.tag`         | Archivo   | 10.5 caps        | 700     | Stamps and switches · `.10em`               |
| `stat`         | Plex Mono | 24 / 1.1         | 500     | Pools 24, attributes 32, ledger cells 20–21 |
| `hand`         | Caveat    | 17–20 / 1.3      | 400–600 | Glosses, asides, captions                   |

### There are three label roles

`.eyebrow`, `.label`, `.tag` — and adding a fourth is how a type system dies.
Before you write a new `letter-spacing` on an uppercase run, check which of the
three it is. If it is genuinely none of them, that is a design conversation, not
a CSS one.

### Narration leading is in px, on purpose

`--narration-leading: 34px` and `--ruling-pitch: 34px` are the same value
because the narration text sits **on** the ruled lines behind it. A unitless
line-height would drift as the font size changed and the text would float off
the ruling. If you change the narration font size, recompute both tokens and
re-check the `background-position` offset on `.gm-message` (currently `0 5px`).

### The drop cap

The first paragraph of a GM beat opens on a 62px wax initial. Add
`.is-continuation` to suppress it on subsequent beats within the same scene —
one drop cap per scene, not one per message, or the transcript turns into a
ransom note.

Selector is `.gm-message p:first-of-type::first-letter`. Note `p:first-of-type`,
not `:first-child`: the `.who` line above it is a `<span>` precisely so it does
not capture the drop cap. If you change `.who` to a `<p>`, the drop cap lands on
the wrong element.
