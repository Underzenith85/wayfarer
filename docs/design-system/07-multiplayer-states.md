# 07 — Multiplayer states

Several players progress one scenario narrated by an LLM. Nearly every hard
design problem in Wayfarer lives here, because these states are concurrent,
partial, and often about _someone else_.

The governing rule: **another player's state may never steal focus, move the
page, or interrupt the read.** Everything below is ambient.

---

## Seat states

Rendered by `.seat-row` in the party panel and the lobby roster.

| State         | Lamp     | Stamp            | Line (hand)                        |
| ------------- | -------- | ---------------- | ---------------------------------- |
| Acting        | lit, wax | amber "Turn"     | "Marisol, writing"                 |
| Drafting      | lit, wax | amber "Turn"     | "Marisol, writing"                 |
| Ready         | unlit    | moss "Ready"     | "Dev, spoke at 21:13"              |
| Held / passed | unlit    | quiet "Waiting"  | "Priya, held"                      |
| Away          | unlit    | quiet "Away"     | "Sam, away six minutes"            |
| Disconnected  | unlit    | iron "Lost"      | "reconnecting…"                    |
| Empty seat    | unlit    | quiet "Empty"    | "open to anyone holding the code"  |
| Building      | unlit    | amber "Building" | "still in the workshop, 118 spent" |

Notes:

- "Writing" is genuinely useful and genuinely ambient. It is in the hand because
  it is a person doing something, not a system status.
- Do **not** render a live per-keystroke indicator. Debounce to a 3-second
  "writing" state that clears after 10 seconds of no input.
- Away time is coarse ("six minutes"), never a ticking counter. A number that
  updates every second is a distraction on a page meant for reading.

## Turn handoff

The most important transition in the product.

- The acting player's `.turn-mark` — "your turn", circled by hand — is the only
  thing that changes in their composer. The composer does not move, resize, or
  gain a border.
- For everyone else the change is the lamp and the stamp in the party panel.
  Nothing in the leaf moves.
- When your turn arrives, the page must **not** scroll, focus the textarea, or
  play a sound. Announce it once via a polite live region and let the player
  finish reading the sentence they are on.

```html
<p class="visually-hidden" role="status">It is your turn.</p>
```

## The GM composing

`.availability-banner`, amber, laid above the composer:

> **The GM is writing** — Marisol's action has been taken and the next beat is
> being composed. You can keep drafting; nothing is locked.

- Never disable the composer while the GM is generating. A player thinking about
  their next move is the whole point of the waiting time.
- If generation streams, narration appears in `.gm-message` progressively. The
  drop cap must render from the first character — do not defer it, or the
  paragraph visibly reflows when it appears.
- If generation fails, `.generation-failure` (iron) replaces the banner in place.
  The player's draft is untouched.

## Provisional narration

The app distinguishes `provisional` narration from committed results
(`workspace.tsx`). This distinction is load-bearing and must be visible:

- Provisional: `.gm-message` with the eyebrow reading "Game master · Provisional
  narration", plus the existing `<small>` note. Render the body at
  `--ink-soft` rather than `--ink`, and drop the wax left border to
  `--rule-strong`. It reads as pencil rather than ink.
- Committed: full ink, wax border.

The rule players need to understand is "story text does not change the committed
result", and the pencil-vs-ink distinction says that without a sentence.

## Concurrent drafts

Every player may draft simultaneously; only the acting player may commit.

- A non-acting player's composer stays fully editable. The primary button reads
  "Waiting on Marisol" and is disabled — the label names _who_, not "wait".
- Drafts are per-player and per-turn, and the draft state line
  (`.composer-draft-state`) says when it was kept, in the hand.
- Never surface another player's draft text. Ever. Drafting is private until
  committed; a leak here changes how people play.

## Clarification requests

`.clarification` — a tipped-in slip inside the transcript, wax edge, addressed
to one player. When the engine needs a clarification:

- Only the addressed player sees the form. Everyone else sees
  `.action-status`: "Awaiting Marisol's clarification".
- It appears **in place** in the transcript, not as a modal. A modal would hide
  the narration the question is about.

## Out-of-character channel

`.player-message.channel-ooc`. Table talk that is not in the fiction: the dashed
margin rule, entirely in the hand, no card. It reads as the margin of the page
because that is exactly what it is.

`.ooc-panel` in the multiplayer panel toggles whether asides are shown. When
hidden, do not leave a gap or a "3 hidden" count — just remove them.

## Disconnection and recovery

- **Your own connection**: `.offline-banner` across the top of the leaf, `--well`
  ground. The composer stays editable; drafts are local and survive.
  On reconnect the banner is replaced by nothing — no "back online" toast.
- **Someone else's**: their seat lamp goes out and the stamp turns iron "Lost".
  Nothing else on the page changes.
- **Resolution arrives after a reconnect**: the transcript backfills in place.
  Do not animate the insertion, and do not scroll the reader to it — mark it and
  let them find it.

## Presence is never a count

No badge counts, no red dots, no "2 players waiting" pills, no toasts. Presence
is four lamps in a column, which a player can read in a glance without leaving
the sentence they are on. That is the whole design.
