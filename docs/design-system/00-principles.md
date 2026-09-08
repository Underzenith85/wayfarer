# 00 — Principles

Wayfarer is a browser app where several players move through one GURPS 4e
scenario narrated by an LLM game master. The design system is called **Field
Journal**. It exists because the default shape of a React app with panels and
cards is a dashboard, and a dashboard is the wrong object: nobody sits down at
a dashboard for three hours to find out what happens next.

The metaphor is a journal kept at the table, under a lamp. Warm paper, iron-gall
ink, ruled pages, wax seals, things pinned in the margin. It is not a costume —
each of the four principles below produces a specific, checkable constraint, and
if a change violates one, the change is wrong even if it looks fine.

---

## 1. Prose is the interface

The GM's narration is the largest text on the screen, in the widest measure,
with the quietest surroundings. Everything else is subordinate.

**Constraints**

- Narration is 19px serif on a 34px leading — larger than any other body text.
- Nothing may sit in the same visual weight class next to it. No cards flanking
  it, no coloured callouts inside it, no icon buttons in its gutter.
- No component may animate while narration is on screen except the voice-capture
  waveform, which only exists while the mic is held.
- If a new feature needs room, it takes it from chrome, not from the measure.

## 2. Numbers live in the margin

A roll is glossed beside the text that prompted it, in the right-hand margin,
the way a player pencils it on a sheet. It never interrupts the read.

**Constraints**

- Roll results never open a modal, a toast, or an inline expanding panel.
- The gloss is `.gloss` in the right column of `.transcript-entry--glossed`.
  Below 1100px it moves under the narration; it never moves _into_ it.
- The arithmetic is in the hand (it belongs to the player). The verdict is a
  stamp (it belongs to the system). Both, always — never one alone.

## 3. Four players, one page

Every seat is visible at all times: whose turn it is, who is drafting, who has
stepped away. Presence is ambient, not a notification.

**Constraints**

- Seat state is shown by a lamp plus a word, in `.seat-row`. Never a toast,
  never a badge count, never a red dot.
- No overlapping-avatar cluster. It duplicates the seat list and it is the most
  recognisable SaaS-header tell there is.
- A player's own activity line ("Marisol, writing") is in the hand — it is a
  human doing something, not a system status.
- Nothing about another player's state may steal focus or move the page.

## 4. Foxing, not glow

Fibre, stains, ruling, tape and a wax seal are things paper does. Ambient
gradients, glass panels, glossy fills and drop shadows that imply floating are
things paper does not do.

**Constraints**

- Gradients are structural only. There are four, and each draws a physical
  feature of paper: the ground on `body`, the margin rule on `.play-workspace`,
  the ruling on `.gm-message`, and the torn edge on `.deckle`. No gradient may
  be used for depth, glow, sheen or a background wash.
- Radius is 2px everywhere. The only `999px` in the system is the lamp's glow.
- Shadows are `--shadow` (a sheet on the desk) and `--lift` (a sheet on another
  sheet). No third elevation.
- No inset highlight on a filled button. That is the glossy-SaaS tell.

---

## The whimsy rule

The handwriting, the stamps, the tilt and the sketches are what make this read
as a game rather than a ticketing system. They are also the fastest way to make
something unreadable, so they have one governing rule, stated in full in
[02 — Typography](02-typography.md):

> **The hand is for what a _person_ wrote. Everything the system asserts stays
> set in type — and every number that must be compared to another number stays
> in the monospace.**

Tilt is capped: ±0.4° on slips, ±1.6° on handwriting, ±4.5° on stamps, ±8° on
dice. Past that it stops reading as a hand and starts reading as a filter.

---

## What this system is not

- **Not a component kit for reuse elsewhere.** It is specific to this product.
  Do not generalise a component until a second real use exists.
- **Not themeable beyond day/night.** There is one brand. `--wax` is not a
  customer-configurable accent.
- **Not Tailwind.** Tailwind v4 is currently in the bundle contributing preflight
  and about twelve unused utilities, and zero utility classes appear in the
  markup. See [10 — Implementation plan](10-implementation-plan.md), phase 0.
