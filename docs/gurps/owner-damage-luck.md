# Owner damage Luck: captured attack consequences and remaining routes

Issue #867 remains open. Typed continuations now cover approved composed Innate
Attack, inventory melee, per-hit inventory ranged and held Fireball damage. These increments
do not certify every owner damage roll. The Luck family remains partial under #854; variants remain with #855.

Source authority: supplied *Characters*, fourth edition, third printing B66,
and *Campaigns*, fourth edition, fourth printing B378–381 and B426. The selected
source files remain private. B66 requires the ordinary decision immediately
after its original and before another roll. An owner's damage seeks the highest
result. A secret damage decision precedes its original. The three ordinary tiers
use the shared real-play cooldown, not campaign time.

## Composed damage transaction

The private `PrepareOwnerDamage` wraps the target's actual `ChooseDefense` or
`ResistComposedAttack` response. Target control authorizes this response; the
attacker's controlling owner separately chooses whether to spend their own Luck.
A new secret preparation needs a currently seated trusted GM. An existing trusted
secret attack declaration retains its secrecy after Luck cancellation, including
a later ordinary target response wrapped by this host. This explicit route
preserves historical ordinary combat command bytes and seeded behavior.

Preparation executes the actual combat preflight and defense options, then
captures delivery, defense, critical table and any Cyclic resistance result.
Those completed prerequisites stay fixed. The original damage is its last roll.
No injury, dependent condition roll, Cyclic occurrence, Symptoms debt, end-turn
settlement or later NPC roll follows before the pending choice. Maximum damage,
misses, successful defenses, immunity and successful resistance have no random
damage opportunity and complete through the canonical path.

The shared TaskSnapshot holds a typed `ComposedDelivery` and
`PreparedOwnerDamage`. They contain immutable source and phase facts, never an
earlier world/resource snapshot to restore. The existing campaign transaction,
global pending gate, Luck reducer and microsecond play clock own admission and
commit. There is no separate cooldown or command ledger.

Choosing accepts the original or selects the best of its two rerolls, then
resumes `apply_trait_attack`, canonical HP/FP injury and combat settlement. Secret
predeclaration draws three complete damage attempts; GM acceptance draws one.
Already delivered damage cannot be cancelled. A choice's selected dice, actual
armor/injury/resources, record, cooldown and pending closure commit together.

The persisted source remains authoritative if the attacker later loses approval
or its already resolved attack target changes. Current control and approved Luck
are still required to spend Luck. Current target armor, approved HT and live HP/FP/posture are read when their
still-unresolved injury is applied. The completed attack, defense, critical,
location and damage expression stay fixed. A changed target can finish its
pending original; a different delivered attack identity is refused before Luck
entropy or cooldown spending. Accepting the original cannot
restore an old actor build or old HP, and an exact retry cannot apply injury twice.

`BeginOpponentAttack(prepare_owner_damage=True)` records the trusted request to
continue a selected opponent attack through its actual defense into this owner's
damage opportunity. The defender's completed result does not expose the next
owner's pending identifier. Opening this phase does not disclose whether the
attacker currently owns Luck; spending it checks current approval and purchase.
This permits separate defender and attacker Luck uses on the same attack, with
separate actor cooldowns and one campaign-wide pending sequence.

Secret dice, candidates and continuation facts remain in GM-only records. A
controlling owner sees an opaque unrolled opportunity; another actor cannot read
the owner's pending damage. Current authority is rechecked under the transaction
lock and on exact retries. Old result secrecy is retained for retry authorization.

## Inventory damage transaction

The melee adapter suspends after attack, defense, critical and location facts,
then resumes the existing armor, injury, equipment and wound tail. The ranged
adapter retains the fixed attack, hit count, defense, critical and ammunition
cost, and opens one immediate damage choice per surviving projectile. Accepting
one hit applies its actual injury and dependent rolls before the next original;
the final hit performs combat settlement once. A six-shot burst with three hits
and selected damages six, two and one moves actual HP10 to4, then2, then1,
consumes ammunition once and records one aggregate wound at final settlement.

An already launched inventory attack retains its approved source build and dice
expression when current approval or the current body changes. Source overrides
are scoped to attack calculation. Settlement and the next action use the current
approved body and live HP/FP; only a revoked approval can use the captured raw
source to close its previously started injury turn. A captured projected build
is never projected a second time.

## Held Fireball damage

The existing held-Fireball resolver captures its paid spell effect, fixed attack,
defense, critical, range, target protection and energy-derived dice expression.
It opens damage before armor, major-wound checks, dropped equipment, spell
release or turn settlement. Selection resumes that canonical tail once; it does
not cast, enlarge, spend casting energy or roll delivery again. The held effect
must still match before Luck entropy or cooldown is spent. Public and inherited
secret source choices use the same shared clock and owner authority.

For the explicit staged generation, B247 1/2D25 and B378 mean selected five
causes five basic damage at24 yards and two at25 or26; selected one becomes
zero at25. Historical ordinary commands retain their prior range generation.

One-energy Fireball selected damage six changes HP10 to4 with shock4 and failed
major-wound stun/prone while caster FP remains9. Original acceptance after
revoked caster approval retains the launched spell source and leaves revocation
in place. This source-specific adapter does not certify other spell effects.

## Independent consequences checked

The domain and real-store suites are `test_prepared_owner_damage`,
`test_owner_damage_host`, `test_owner_damage_boundaries`,
`test_owner_damage_inventory`, `test_owner_damage_ranged`,
`test_owner_damage_source_authority`, `test_owner_damage_visibility` and
`test_owner_damage_missile` and `test_owner_damage_current_target`:

- A selected 2d result of 11 against DR5 with divisor2 gives effective DR2 and
  nine burning injury. Actual HP10 becomes1, shock is4, and a failed major-wound
  HT check causes stun and prone. At the exact half-damage boundary, floor(11/2)
  becomes5 basic and three injury after DR
- A fixed triple-damage critical multiplies the selected roll, without rerolling
  its prior critical table. Maximum damage does not manufacture a Luck roll
- Six fatigue damage against current FP2 results in FP−4 and four HP injury
  through the actual signed fatigue ledger
- Selected eight burning injury creates eight total damage debt, its two actual
  Symptoms effects and one source-bound Cyclic occurrence; unselected attempts
  create none of these consequences
- Ordinary and secret exact retries, rollback, competing independent stores,
  full event folding and seed-only command reexecution retain selected state
- All three tier deadlines retain exact microseconds. Waiting cannot legalize an
  originally unavailable ordinary use; an unrolled secret declaration can become
  eligible at the later exact deadline. Pause/resume preserves the original

Passing this evidence does not promote unrelated consumer rows.

## Complete route map and open work

The live consumer map remains larger than the completed increments:

| Route | Current damage consumer | #867 status |
| --- | --- | --- |
| Approved Innate Attack and Malediction | `traits/composed_resolution` → `traits/attack_defense` → canonical injury/fatigue, Cyclic and Symptoms | Typed composed preparation/selection implemented; source-specific unsupported modifiers remain outside this host |
| Inventory melee | `combat/melee/resolution` | Typed damage staging and actual sword injury/turn continuation implemented; sacrificial defense and queued attack regressions preserve canonical ordering |
| Inventory ranged and thrown weapons | `combat/ranged/resolution` | Typed per-hit staging and real rapid-fire damage/paid ammunition implemented; selected shield/object, cover, overpenetration and follow-up source-specific oracle coverage remains open |
| Unarmed strikes and armed parry injury | `combat/unarmed/resolution`, `combat/unarmed/injury` | Pending owner damage and downstream self-injury/grapple consequences still required |
| Shield rush and collisions | `combat/shield_rush`, movement collision reducers | Distinct attacking and reciprocal rolls require separate lawful ownership and continuation boundaries |
| Thrown explosives and fragments | `combat/thrown/explosions` | Blast/fragment ownership and multiple-target continuations remain unverified |
| Lightweight authored combat profiles | `combat/lite_resolution` | No new Basic Set owner Luck consumer is certified by the composed tests |
| Held Fireball and other spell damage | `magic/missiles` and other magic attack/effect consumers | Held Fireball has a typed selected-damage continuation with paid energy and once-only release; other spell damage consumers remain open |
| Critical self-hit, fall/collision, scheduled Cyclic and environmental damage | Critical, movement and health reducers | Some are consequences or outside events rather than the owner's chosen attack; source classification and lawful pending boundary remain explicit work, including #870 where applicable |

No retroactive replay-and-replace strategy can close those rows: their current
reducers often draw additional dice and commit multiple dependent consequences
after damage. Each adapter must capture the actual unresolved phase before those
consequences and prove its selected state through a real command on both stores.
Public transport/UI/voice changes and version changes remain out of scope.
