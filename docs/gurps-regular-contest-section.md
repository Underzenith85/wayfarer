# B349 Regular Contests execution obligation

The bounded closure target is exactly
`section:campaigns:b349:regular-contests`, using the supplied Campaigns fourth
printing and its frozen SHA-256 identity. This covers the Regular Contest
procedure, including its Extreme Scores paragraphs. It does not certify all
skills, grappling, turn clocks, other contests or the parent success-roll chapter.

Acceptance requires all of the following:

- Exactly one successful contestant wins. Both failures or both successes
  preserve the contest and require another round. A finite server round limit
  refuses an undecided result instead of inventing a winner.
- Levelling uses effective scores. Both scores at most 6, or both at least 14,
  normalize the lower to 10 by a shared offset. When both exceed 20, scaling
  sets the lower to 10 and floors the higher proportionally. Printed examples,
  inclusive thresholds, adjacent boundaries, reversed ordering and modifier
  composition remain independently checked.
- The shared procedure reaches an actual B370 pin. At ST 15 versus 15, both
  targets become 10. Scripted totals 12/11 both fail, 9/9 both succeed, and
  9/11 split: the first two rounds preserve the grip and the third pins. The
  host checks both SQLite and PostgreSQL, current authority/revision, unchanged
  HP/FP, exact retry after restart and event folding. A separate genuinely
  seeded campaign reexecutes from original genesis; scripted dice are not
  passed off as server-seed replay.
- Recorded historical adjustment reasons and host generations retain their
  previous bytes and scoring. Tampered or mixed receipts fail replay. The
  unavailable Lite source and its existing behavior are unchanged.
- Execution requires every selected Basic Set Regular Contest source case.
  The live pin case additionally requires both backend nodes and its pure
  source-case node. Missing, skipped, failed or stale evidence for any required
  node blocks the section, including loss of only the PostgreSQL proof.

The source fixtures and review fingerprints document the earlier correction of
incorrect low/high levelling expectations; see
[the selected-source review](gurps-basic-set-source-review.md). The independent
live oracle is `basic-regular-contest-live-pin-rounds`, executed by
`tests/test_regular_contest_section.py` after its consequence and replay checks.
The section binding is explicit and confers no execution credit on its parent
or on unrelated inventory rows.

Five additional independent boundary cases make the adjacent domains explicit:
7/6 and 14/13 stay unchanged, 21/20 uses an offset to 11/10, reversed 500/660
scales to 10/13, and base 560 plus a +100 modifier versus 500 scales to 13/10.
The modifier case passes each contestant's actual modifiers into the shared
scorer; it cannot pass merely by replacing base scores with expected values.

Re-review of callers also found that Characters B202 Interrogation requires a
Quick Contest, while B197 player-versus-player Gambling requires a Regular
Contest. Those skill-specific corrections are separate obligations. Historical
Interrogation commands using Regular Contests are compatibility evidence, not
current source-correct evidence for this section or for Interrogation. B202's
five-minute question lifecycle, missing situational bindings and knowledge
disclosure secrecy remain explicitly open in its own source review.

Source dispositions and broad capability statuses are unchanged. The section
is executable only when its required nodes pass against the exact current
checkout; a registry entry alone is insufficient.
