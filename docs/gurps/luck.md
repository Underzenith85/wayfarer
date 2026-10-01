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

Remaining consumer integrations are tracked in #854; the three special
limitation constructions are tracked in #855. Both remain unverified.

## Ordinary task consumer and limited constructions (#866/#855)

`simulation.campaign.luck.apply_lucky_task` joins one pending worker check to the
existing long-task reducer. Its selected dice change actual progress, completion,
and the persisted campaign clock; the host must save both snapshots under one
transaction lock. Committed work cannot be rerolled. Separate supervisor checks
remain unsupported until their own pending identities are represented.

The selected B66 Active construction costs -40% and requires declaration before
any original dice. Aspected costs -20%; the currently bounded classes are
athletics, social interactions, job tasks, and the source's precise combat subset
(weapon checks, active defenses, and close-combat ST/DX). A generic combat-adjacent
check does not qualify. Defensive costs -20% and restricts the trusted pending
record to failed active defenses, resistance or injury HT checks, or an incoming
critical attack. Aspect identity and failed status are trusted host facts, never
player arithmetic. Purchase prices use the existing canonical modifier engine;
cooldown always comes from the base 15/30/60-point tier.

Active and job-aspected Luck have actual long-task progress tests. Defensive and
other aspects still need their downstream defense/social/combat consumers, so
#855 remains open. #866 remains open for other ordinary skill consumers and
supervised work. #854 still includes damage, reaction, attacker, outside-event
and secret-GM integrations. This supersedes the blanket modified-construction
rejection above without promoting any global certification status.
