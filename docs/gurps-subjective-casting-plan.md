# Actor-relative Great Haste combat casting

This isolated lane starts at `8f31e5c91b99507148795c95819c42330eb4bc1f`.
It does not change the published Great Haste candidate or close issue #797.

## Source contract

Basic Set Characters B236 requires consecutive Concentrate maneuvers for a
multi-second spell and rolls at the end of the final one. B38 expressly allows
Altered Time Rate to speed spells through multiple Concentrate maneuvers during
one existing turn. Great Haste B251 requires three casting seconds before skill
reduction, then lasts ten real seconds. Campaigns B366 allows a Step and defenses
while concentrating; distraction requires the existing Will-3 concentration
check. Private PDFs and extracted prose stay outside the repository.

## Actual host execution

`test_great_haste_combat_concentration.py` constructs an approved native ATR1
caster, including the canonical approval ledger and matching resource owner
prerequisites, and starts a real encounter. Starting Great Haste spends the first
Concentrate opportunity; continuing spends the second without moving the shared
clock. Two casting seconds have elapsed subjectively. After the other actor's
real turn, the caster's first next Concentrate completes the third casting second,
rolls and spends 5 FP at real time 1. The caster still owns their second native
opportunity. The subject's effect expires at real time 11.

The authenticated private adapter now executes this sequence through the canonical
combat settlement boundary. Both stores also exercise ordinary three-turn casting,
exact retries, seeded reexecution, original request bytes, and historical
outside-combat execution without the new private generation.

## Trusted joins

* Capture a private Great Haste combat-casting generation in the durable input.
  An absent feature retains the original outside-combat restriction. Exact
  retries capture the original feature and preserve the original request hash.
  Seeded reexecution receives that feature through its private replay context.
* Authorize only the current actor's real Concentrate opportunity. Bind current
  canonical target, living-body eligibility, mana, approved skill, cost and range.
  Never treat a naked spell operation as a free additional action.
* Seed the existing maneuver budget before accepting casting. Advance one
  subjective casting second per accepted start/concentrate operation. Require
  consecutive casting opportunities, and use the existing distraction state and
  Will-3 check; another maneuver interrupts the pending cast.
* Add a private scoped timing hook to the shared spell reducer: a trusted combat
  adapter can fulfill required casting maneuvers without the outside-combat
  shared-clock equality test. Never change canonical game_time to trick that
  equality test. Outside-combat and historical spell execution retain it.
* Automatically complete during the final Concentrate, with current target/range
  and source checks immediately before dice and payment. The existing voluntary
  `complete` operation remains unavailable during combat.
* Settle through the existing combat turn boundary. Injury start runs on the
  first real-turn opportunity and injury end on the last. Preserve defenses,
  native opportunities, existing pending responses and one outer revision.
  Real-clock advancement occurs only when the initiative cycle advances.
* Save Great Haste activation and end-fatigue witnesses only after successful
  completion, using real completion time for the ten-second expiry. Reject
  unsupported overlap and mid-turn activation rather than minting opportunities.

Current combat admission requires a known, currently visible nonself subject,
including the configured hex board line of sight. Daze, due explosions, pending
responses and ordinary recovery guards reject before casting consumes an
opportunity. Cast approval and current range are checked again at completion.
Self activation during combat, unseen subjects, casting Steps, and general
subjective timing for other spells remain unsupported. The external clock
negative uses the canonical domain clock under the store lock; it does not claim
a public Wait sequence or seeded replay of a custom fixture command.

The isolated implementation does not close the whole issue #797 contract.
Public spell IDs, public command schemas and historical spell events stay closed.
