# 06 — Screens

Anatomy for every screen the codebase contains, including the ones not drawn on
the canvas. File paths are into `frontend/src/`.

Routing is TanStack Router; segments are defined in `routes.ts` as
`"" | character | inventory | journal | campaign`, with a separate setup shell.

---

## Sign-in / mode select — `play/connection.tsx`

Three `.mode-tab`s: Start game · Join game · Create scenario. These are
exclusive tasks: starting instantiates a published scenario, joining opens an
invitation or saved game, and scenario creation edits reusable source material.
Access-token field, then `Sign in`.

- Centred column, `max-width: 54rem`, no rail and no party panel.
- The masthead is the compass, the wordmark, and the campaign line in the hand.
- Tabs are underscored, never filled.
- The token field is a well with a visible `.label`. Never a placeholder-as-label.

## Setup lobby — `setup/lobby.tsx` (1305 lines — the biggest screen)

`.setup-lobby` centred column. Starting a game asks for one published
adventure, then rules, then review; it never authors a second premise.
`.setup-steps` is the numbered progress strip (collapses to 44px circles below
700px), `.step-nav` the back/next footer, and `.setup-review` the final
summary list. Saved and invited games live only under Join game.

Drawn on the canvas as **Table lobby**. The pieces:

- **Scenario card** — tipped-in, taped, with a drop cap on the pitch and a
  `.facts` column of length / tone / combat / TL / seats in the hand.
- **Seat roster** — `.seat-row` per player, lamp + name + what they are doing +
  status stamp. Includes the empty seat as a row, not as a dashed placeholder.
- **Invite code** — `.code .val`, monospace, `.22em` tracking, dashed well.
- **Narration mode** — three `.opt` rows, the chosen one wax-bordered.
- **Start bar** — dice, one line in the hand about server-side rolling, then
  outline + primary.

## Character workshop — `character/workshop.tsx`, `character/draft-editor.tsx`

The point-buy editor. `.point-budget` is sticky at the top and stays visible for
the whole flow; `[data-overspent="true"]` turns it iron.

- `.attribute-row` — ST/DX/IQ/HT with `.purchase-stepper`
- `.purchase-row` — one bought trait: select, stepper, `.purchase-cost`,
  `.purchase-remove` (36px, iron on hover)
- `.derived-preview dl` — live Will/Per/Speed/Move/Dodge/Parry. **Name the
  modifier** under any derived value that includes one.
- `.guided-authoring` / `.guided-fields` — the LLM-assisted concept step
- `.concept-conflict` — an amber notice slip when the generated concept
  disagrees with what was bought
- `.party-character` — a collapsible per-character block when building several

**Rule:** every number in this screen is monospace. The only handwriting is the
remainder in the budget bar and the note under a threshold.

## Play — `play/workspace.tsx`

Drawn as **Play — day** and **Play — lamplight**. Three columns.

**Rail** (`aside.navigation-panel`) — Scene · Character · Party · Inventory ·
Journal, wax-filled when `.active`, plus `.table-note` at the bottom carrying
the campaign `<Seal>` and the in-world date.

**Leaf** (`.play-workspace`) — the wax margin rule at 64px, then:

- `.page-heading` — eyebrow, scene title, and the conditions of the scene in
  the hand ("night, third watch / no light — everyone at −2")
- `.transcript` — `<li class="transcript-entry">` per turn, containing
  `.player-message` (+ `channel-action|dialogue|ooc`), `.action-status`
  (`role="status"`), `.committed-result`, and `.gm-message`
- `.composer` — pinned to the bottom by `margin-top: auto`

**Party panel** (`aside.character-panel`) — seats, the acting character's pools
and mini stat table, condition stamps, and a pinned sketch.

### The roll slip is real data

`.committed-result` currently renders `a.resolution.checks` as sentences inside a
`<details>`. The contract (`api/contracts.generated.ts`, `Check`) is:

```ts
{ label: string; dice: number[]; target: number; margin: number;
  outcome: "success" | "failure" | "critical_success" | "critical_failure" }
```

which maps onto `.slip` with nothing left over:

| Field     | Slot                          | Voice     |
| --------- | ----------------------------- | --------- |
| `label`   | `.slip-what b`                | sans caps |
| `target`  | `.slip-what span`             | hand      |
| `dice`    | `<Dice faces={check.dice} />` | drawn     |
| `outcome` | `.slip-verdict .stamp`        | stamp     |
| `margin`  | `.slip-verdict`               | hand      |

Outcome → stamp: `success` → moss "Made it", `failure` → iron "Missed",
`critical_success` → wax "Critical", `critical_failure` → iron "Critical miss".

Take the checks **out of the `<details>`**. They are the most interesting thing
on the screen and they are currently collapsed behind a disclosure.

## Character sheet — `character/pages.tsx`

Drawn as **Character sheet**. `.character-banner`, `.stat-grid` (ST/DX/IQ/HT),
pools, `.derived-grid`, three `.sheet-section` ledgers (skills, advantages,
disadvantages), `.points-grid`, `.condition-list`.

**The point ledger must add up.** Five cells — attributes, advantages,
disadvantages, skills, spent-of-total — and the fifth is the sum of the first
four. A sheet whose displayed subtotals do not reconcile is a bug a GURPS player
will find in about four seconds.

Derived values that include a modifier must name it: Dodge 9 with `+1 reflexes`
underneath, Fright 15 with `+2 reflexes`. Otherwise players double-count.

## Inventory — `character/pages.tsx` (`InventoryPage`)

`.currency-strip`, `.inventory-totals`, `.inventory-filters` (search + sort),
`.item-list` of `.item-card`, then item detail with `.item-facts` and
`.item-operation-form`. `.custody-note` — who is actually carrying it — is in
the hand, because custody is a table fact, not a system one.

Encumbrance is the number that matters here: show it as a `.track` against the
character's ST-derived thresholds, not as a bare figure.

## Party / multiplayer — `multiplayer/panel.tsx`

`.multiplayer-panel` with `.presence-list`, `.group-controls`, `.ooc-panel`,
`.session-recap`. See [07 — Multiplayer states](07-multiplayer-states.md).

## Tactical — `play/tactical.tsx`

`.tactical-layout` (map + action list). The map ground moves from `#101824` — a
slate that belongs to a different product — to `--well` with a `--rule-strong`
border, so it reads as a drawn plan on the same paper. Tokens, ranges and
facings are drawn marks; keep the same 1.1–1.2 stroke weight as the ornaments.

`.tactical-actions` is a scrolling list, max-height 24rem. Each action is a
36px-minimum control.

## Session closure and advancement — `adventure/pages.tsx` (`SessionClosure`)

`.closure-stack` → `.closure-hero`, `.closure-section`, `.closure-row`.
Objectives carry `.outcome` stamps (achieved / partial / failed).
`.advancement-option` is a radio row that takes a wax border when chosen.
`.lifecycle-actions` / `.lifecycle-undo` is the end-session control with its
undo window.

This screen is the end of a three-hour session, so it earns ceremony: the
campaign seal, a fleuron above the advancement block, and the earned points
written in the hand.

## Journal / campaign — `adventure/pages.tsx` (`DiscoveryJournal`), `play/workspace.tsx` (`CampaignHome`)

`.campaign-grid` of `.campaign-card`, `.adventure-journal`, `.session-recap`.
The journal is the one place a ruled ground beyond narration would be
legitimate, since it _is_ the journal — but do it deliberately, not by accident.

## Voice — `voice/input.tsx`, `voice/narration.tsx`

`.voice-mic` (`aria-pressed`), `.voice-wave`, `.voice-status`, `.voice-notice`.
The waveform is the only animation in the product and it exists only while the
mic is held. Both animations stop under `prefers-reduced-motion`.

## Global states

| State                | Class                                  | Treatment                          |
| -------------------- | -------------------------------------- | ---------------------------------- |
| Offline              | `.offline-banner`                      | slip, `--well` ground, faint edge  |
| Provider unavailable | `.availability-banner`                 | slip, amber edge                   |
| Request failed       | `.request-error`                       | slip, iron edge                    |
| Generation failed    | `.generation-failure`                  | slip, iron edge                    |
| Loading              | `.skeleton`                            | `--well` block, **no shimmer**     |
| Empty                | `.empty-region`                        | in the hand — a note, not an error |
| Withheld by the GM   | `.section-withheld`, `.scene-withheld` | in the hand                        |
| Unavailable region   | `.unavailable-region`                  | amber tipped-in slip               |
| Confirm              | `.confirm-content` + `.sheet-overlay`  | focus trap, Escape closes          |

There are no toasts. A message worth showing is worth leaving on the page until
it stops being true.
