# Luck (B66), issue #765

Source checked: supplied *Characters*, fourth edition, third printing, printed
B66. Ordinary Luck costs 15 points and waits 3,600 elapsed seconds of real play;
Extraordinary costs 30 and waits 1,800; Ridiculous costs 60 and waits 600. The
wait starts at the last use. Neither crossing a clock hour, advancing campaign
time nor waiting through several cooldowns creates banked uses.

`simulation.traits.luck` joins the canonical approved purchase to these tiers.
For an immediate pending own roll it retains the original and rolls the same
expression twice. Own success selects the lowest total; damage and reaction
select the highest. Attack rolls select the attacker's worst. An authored outside
party event must include the owner among its affected actors; Luck is not
transferable to another character's personal task. A secret GM roll requires
predeclaration and draws all three attempts. Dice and use receipts persist in a
strict immutable snapshot. Secret receipts are GM-only in the visibility projection.

The runtime clock is trusted elapsed **real-play** time supplied by the host;
`game_time` never satisfies a cooldown. SeededRandom consumes persisted command
entropy. Full command equality governs replay; stale revisions, changed intent,
unauthorized callers, unavailable purchases and already-resolved rolls reject.

`simulation.skills.luck.apply_lucky_cinematic_skill` is the concrete consumer:
it scores the chosen dice through the existing cinematic skill reducer and
commits its outcome, margin and FP alongside the Luck snapshot. A host must
persist both snapshots atomically. Failed validation returns neither snapshot.
The original roll must still be pending; previously committed consequences cannot
be retroactively rewritten. Tests demonstrate an original critical failure
becoming an actual persisted success, followed by an exact no-reroll retry.

Scope still unverified: Active, Aspected and Defensive purchase variants (rejected
by this runtime), and host wiring of other success tasks, combat, damage,
reaction, party-event and secret-roll consumers. Their generic pending dice
selection is tested, but that does not certify each downstream rule family.
No global or parent certification status is promoted by this change.
