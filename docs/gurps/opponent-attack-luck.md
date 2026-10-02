# Opponent attack Luck (B66, #869)

Sources: supplied *Characters*, fourth edition, third printing B66 and B106, and
*Campaigns*, fourth edition, fourth printing B348–349, B371–375 and B382. Source
PDFs and extracted pages remain private. The private TaskService commands use
the existing campaign transaction, pending ledger, Luck reducer and microsecond
real-play clock. They add no API, UI, voice operation or public schema.

`BeginOpponentAttack` is a trusted GM preparation naming an existing unresolved
attack against its controlling owner. Its recorded public/secret visibility choice
is source authority; the victim cannot disclose an opponent check by guessing an
attack identity. The engine captures the original 3d6 and the actual canonical target before
any dependent defense, critical table, hit or damage consequence. The declaration's
already spent turn, movement, aim and resources remain committed. Target preparation
uses the canonical resolver's own formula and draws no dice of its own.

`ChooseOpponentAttack` either accepts that original or spends the victim's Luck.
B66's opponent branch selects the highest total of the three attempts, keeping the
earlier attempt on ties. The selected faces are scored against the original's
captured target and modifiers. The actual ordinary defense command then uses current
defense eligibility, pays its effort once and resolves the canonical consequences.
The trusted GM may bind a subsequent damage phase in the original preparation.
A victim's choice cannot add that phase or probe the attacker's Luck. Preparation
is independent of the attacker's Luck purchase; fresh use checks current eligibility.
The victim's completed choice reveals no subsequent attacker pending identity.
The attacker or GM retrieves that private phase through its existing authority.

The global immediate-choice guard prevents another consequential roll between the
original and the decision; this serialization is host policy implementing B66's
immediate timing. Cooldown eligibility is checked both at the original and at use,
so waiting over a deadline while observing the roll cannot make an unavailable use
legal. Accepting spends no Luck. Fresh and exact-retry decisions recheck current
control. The deployment's GM seat may accept an original, but cannot spend another
player's Luck merely by being the GM.

Previously committed interception rolls and composed critical continuations are
closed to reopening. A selected attack preserves its original source and target;
current defense protection is evaluated separately. Captured attacks carry their launched source facts through later approval loss.
Current defender protection and live HP/FP remain authoritative. End-turn handling
uses a current approved body when available and the captured source only as the
fallback needed to finish that already spent turn after approval is removed.
Nothing restores an actor, approval, item, resource or earlier world snapshot.

## Actual entry-route map

| Entry | Canonical attack boundary | Current evidence |
| --- | --- | --- |
| Inventory melee | `combat.melee.resolution.resolve_melee` before attack 3d | Real sword miss/hit and injury; actual source changes preserve live body and resources |
| Later Double/Rapid Strike blow and Wait reaction/resume | Same pending melee attack boundary, with distinct originals | Actual first miss, later hit, once-only wounds, one cooldown, preserved Wait order |
| Ranged inventory | `combat.ranged.resolution.resolve` before attack 3d and malfunction consequences | Real rapid-fire selection changes hit count and injury; loaded ammunition is spent once |
| Spray and queued suppression targets | Separate canonical ranged pending attack per target/zone | Actual target-specific misses/hits, traversal shots, prepaid suppression ammunition and later cooldown refusal |
| Thrown/explosive projectile delivery | Same canonical ranged attack before scatter and payload scheduling | Adapter present; dedicated Luck explosive/scatter consequence oracle remains open |
| Ordinary approved composed Innate Attack | `traits.composed_resolution` before defense | Real source-bound hit, Dodge, damage, both owners' Luck, source-change continuation |
| Unarmed punch/kick/grapple/arm-lock pending attack | `combat.unarmed.resolution.defend` before attack 3d and random location | Real punch injury and acquired/avoided grapple; valid and revoked source cases |
| Private random unarmed strike | Same canonical unarmed attack before location and balance checks | Actual blind kick worst check, once-only location dice and balance check |
| Non-direct fragmentation against an actor | Inline `combat.thrown.explosions.resolve`, skill 15 plus range/size/posture | Real source-permitted attack roll; no staged Luck continuation yet, so #869 remains open |
| Direct fragmentation and blast concussion | Automatic source damage, with applicable defense/damage procedures | No independent opponent attack original for the automatic hit; damage scope is separate |
| Shield rush | `combat.shield_rush` before attack 3d | Real collision injury, reciprocal shield damage, knockdown, preserved movement |
| Released held Fireball | `magic.missiles.resolve` before attack 3d | Real cast/release energy retention, selected hit/miss and once-only effect release |
| Malediction 1 | `traits.malediction_checks` captures the caster Will check before its separate resistance | Real consent/resistance, failed caster, tied margin, Rule of 16 and actual injury; fixed declared resistance |
| Immediate grappling control contests/lock injury | `combat.unarmed.control` | Separate checks outside this unresolved attack-roll adapter |
| Legacy non-GURPS AttackProfile | `combat.lite_resolution` | Not evidence for the Basic Set Luck purchase |
| Ordinary narrative `Attack` action | ActionEngine assessment | Still returns `combat.not_implemented`; no manufactured attack roll |

No-defense and unaware victims retain the pending attack opportunity. Trusted
roll visibility does not grant an active defense. An original victim can request
the opportunity before an interception; already committed interception/critical
traces cannot be reopened. Source procedures with no attack roll are distinct
from executable inline attack rolls that lack a continuation. In particular,
non-direct fragmentation remains an explicit implementation gap, not a no-roll
exception. Already resolved injury is immutable.

## Evidence and remaining acceptance

The composed and inventory host suites exercise real SQLite and PostgreSQL
stores, exact microsecond deadlines for all three ordinary tiers, pause/resume,
current authority, duplicate/distinct-store races, rollback after a candidate
commit, restart, complete event folding and seed-only command reexecution.
Independent numeric cases include target 12 with attempts 3/9/15 selecting 15
and dealing no injury; selected 9 permits a successful Dodge or 4 points of
burning injury without a defense. An inventory burst at target 14 with selected
10 yields three 1-point hits, and expends the original six-shot load once.

Trusted secret preparation captures a source-bound unrolled context. The owner
can predeclare Luck, causing three private attacks to be drawn and the worst to
be selected; a trusted GM can resolve one attempt or cancel before any target
dice. Cancellation preserves the underlying spent declaration and closes only
the Luck opportunity. Its source secrecy survives cancellation, ordinary defense
or resistance, restart and exact retry. Raw enclosing stream receipts obey that
same audience, and a former GM cannot regain historical visibility by retrying.
Unrolled preparation time is not a cooldown eligibility time. Secret results, attempts, target numbers and modifiers stay GM-only; actor
hex summaries retain actual consequences but omit their numeric check totals and
targets. A following secret owner-damage phase remains unrolled and private.

Malediction 1 records the declared consent/resistance before the caster original.
When resisted, the caster target includes the B349 Rule of 16 cap before its dice.
The selected caster check is immutable. A failed caster terminates without a
fabricated resistance or any resistance dice. A successful caster rolls the target's
still-unresolved resistance using its current approved Will and conditions,
without changing the earlier caster target or Rule of 16 cap. B348's contest adjudicator decides actual damage, including the
resister winning tied margins. Consent still requires the caster to succeed.
The approved composed source currently refuses Malediction 2/3 at purchase
admission; this consumer does not silently broaden that support.

The full issue remains open until final both-store, replay and independent
acceptance of these routes and combined phases has completed. No
unrelated evidence row, entire Luck family or whole-engine certification is
promoted. Active, Aspected and Defensive Luck remain #855 scope.
