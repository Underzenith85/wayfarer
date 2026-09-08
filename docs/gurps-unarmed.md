# Basic Set unarmed combat and grapple control

Issue #108 adds internal commands to the existing `CombatService` transaction. The selected profile must be exactly `gurps-basic-set-4e-2004`, and both participants must have explicit living-human anatomy. Existing campaign pins and square coordinates retain their meaning. The hex geometry contracts from #105 remain separate; #115 owns the versioned map/API/UI integration.

## Supported commands

| Command/action | Behavior |
| --- | --- |
| `TakeUnarmedTurn`: `punch`, `kick` | Compiled DX or selected striking skill, thrust-based crushing damage and per-die training bonus. Punches require a named free hand; kicks name a foot. A missed kick checks balance. Armor and resulting wounds use the existing injury service, including injury to a bare striking limb against DR 3+. |
| `grapple` | Explicit one/two-hand control of torso, neck, arm or leg; reach C and explicit entry into the target's square. No damage on initiation. Torso control imposes the DX-related attack/defense penalties; a controlled arm cannot strike or parry. |
| `ChooseDefense` | The existing authenticated command resolves an unarmed pause. Supported choices are Dodge, barehanded Parry, an explicitly selected armed Parry, and no defense. Weapon damage modes are selected explicitly in tactical v2; ambiguous modes are rejected before dice. All-Out Defense (Double) permits an ordered fallback using a different defense or a different free parrying hand. Both choices are validated before dice; the fallback rolls only after an ordinary failure. Ordered choices and actual checks survive receipt replay. No other combat command can bypass the pause. |
| `break_free` | One Quick Contest with grip, pin, stun and lock modifiers. A failed arm-lock escape makes subsequent attempts harder. Pin escape attempts have a ten-round interval. |
| `takedown` | One Quick Contest using ST, DX or grappling skill against a standing opponent. The loser falls and loses the reciprocal grip. |
| `pin` | One Regular Contest round, using the existing contest normalization. The free-hand advantage is included. Both-success/both-failure leaves control unchanged and requires another turn, without rolling ahead in time. |
| `arm_lock` | Offensive path from a surviving, two-hand Judo/Wrestling grapple on an earlier turn; an attack/defense pause captures the selected arm. |
| `lock_damage` | Once on each subsequent holder turn, a passive contest applies crushing damage to the arm, excluding flexible armor. The action does not consume the holder's attack. Winning a contest on an already crippled arm applies shock and knockdown/stun checks through the injury service, without losing more HP or adding another crippling injury. |
| `strangle` | A neck-grip contest applies crushing neck damage. Penetrating injury starts the existing durable suffocation schedule; one-hand use carries its penalty. |
| `ResolveChokeEffects` | The victim settles a due grip-specific suffocation deadline. Existing hazard/fatigue logic owns FP, consciousness and the no-air deadline. It consumes no combat turn and cannot duplicate a tick. |
| `TakeCombatTurn`: `wait` | The existing Wait maneuver may declare an unarmed reaction instead of a weapon one, and a grappled or grappling fighter may declare it. Any turn-consuming unarmed action then pauses the encounter before dice; the waiter takes exactly the declared attack or declines. See [unarmed Wait reactions](#unarmed-wait-reactions). |
| `release` | Free release on the holder's turn, including a selected subset of hands. An arm lock cannot retain only one hand. Releasing a choking grip ends its hazard after due effects are settled. |

The suffocation adapter uses the existing one-second shared combat clock. It does not introduce a second clock or a player-authored damage parameter. Individual-actor phase timing for choking, alongside other tactical timing refinements, remains part of #176.

## Persistence and authority

Grips, hand commitments, close-combat relationships, arm-lock escape penalties, pin deadlines, unarmed defense intent and check traces persist in `Encounter`. Grip ownership and distinct-hand invariants are validated on reload. HP, FP and suffocation use the existing resource state and receipts. A third-party knockout or disabling injury to the holder releases unusable grips, while an unconscious target can remain held. Ending an encounter does not silently cure a choking victim.

Commands use the existing authenticated actor check, canonical payload digest, campaign revision/CAS and committed result replay. A different payload cannot reuse a command ID. Recovery, ability and party transitions recognize the unarmed pause. Noncombat actions already reject actors in an active encounter. No frozen v1 schema is changed and no new LLM capability is silently enabled.

## Evidence and limits

The declared source is Basic Set Fourth Edition, first printing (2004), with the January 26, 2007 first-printing errata baseline. Numeric references: Characters B182, B203, B228 and B271; Campaigns B349, B366, B370-371, B379, B400, B403 and B436. Tests contain numeric expectations and references, not rulebook prose. Independent cases are in the common conformance ledger and `tests/test_unarmed.py`. The exact printing/errata artifact audit remains outstanding; the implementation is not a conformance certificate.

Both `gurps.combat.unarmed` and `gurps.combat.grappling` remain **partial**, which keeps the existing scenario/character capability checks fail-closed. #108 remains open. [Follow-up #176](https://github.com/Underzenith85/wayfarer/issues/176) tracks the remaining work:

- Remaining unarmed critical-miss consequences: knockout/recovery (3/18), attacking stumble displacement (7/14), dropped-guard Evaluate/Feint timing (13), torn-muscle lasting penalties (15), and falling onto a ready impaling weapon (5/6/16). Armed critical-parry failures also retain an explicit blocker. These outcomes keep their recorded dice and halt continuation.
- All-Out Attack Double/Feint, movement paths beyond the existing close-combat entry, two-handed Wrestling/Sumo parries, remaining skill-specific defenses, and retreat/following during control attacks. Wait is integrated below; Evaluate, Feint, Aim and Concentrate while a grip is held remain explicitly rejected.
- The defensive parry-to-arm-lock route and the distinct Choke Hold technique.
- Escape steps, dragging/carrying, twice-ST movement exceptions, Size Modifier/multiarm variants and additional strikes/targets. Unsupported movement/reload/maneuver combinations are rejected explicitly.

Current bodies have no authored Size Modifier, so tests cover equal-sized human participants. This does not implement large/small creature grappling. Optional/supplement grappling systems and control points are excluded.

The Double Defense subset of #176 has restart, duplicate-receipt, pre-dice rejection, distinct-hand, fallback ordering and critical-blocker regression tests in `tests/test_unarmed_double_defense.py`. Unimplemented contextual critical outcomes still preserve table dice and block continuation, including when reached through the fallback. This subset does not complete #176 or the source-baseline audit.

## Additional #176 integrations

- All B556 non-head critical-hit table entries resolve for the supported strikes. Critical hits bypass active defenses, while damage, DR reduction, major-wound checks, double shock, transient limb injury and forced item drops use authoritative injury/equipment state.
- B557 strain (4/17), solid-object self-injury without an impaling-weapon exception (5/6/16), falls (8 and parrying 7/14), lost balance (9-11), and trip checks (12) execute immediately. Limb strain lasts 1,800 seconds and self-injury uses the attacking limb, without treating it as a breakable weapon. A subject already prone takes the table's general injury for an unarmed-table fall. B382 critical Dodge failure falls without table dice; critical defense success applies the attacker's unarmed miss table. Contextual results listed above remain blockers.
- Armed parries roll a separate weapon-skill check to injure the attacking arm or leg. Judo/Karate attacks impose the B376 -4 on that check. The selected weapon mode supplies damage type, armor divisor and damage dice. Extra checks and dice persist in the unarmed trace and in injury receipts.
- All-Out Attack (Determined/Strong) and stationary/close-entry Move and Attack retain their attack modifiers and defense restrictions. Existing Evaluate/Feint benefits apply to the immediately following unarmed attack and cannot be reused on later attacks. Judo/Karate use encumbrance penalties; Boxing's kick-parry penalty participates in automatic best-defense selection.
- Punches and kicks can target torso, neck, arms or legs. A neck strike missed by one resolves against the torso and records both intent and resolved location. Hex retreat is supported against strikes, with the trained unarmed parry bonus. Legacy square coordinates are unchanged.
- Ready while grappling requires explicitly selected free hands. A grappled actor makes a DX check (including applicable shock/control penalties); failure drops only the selected item. The check and result survive restart and command replay. Partial release does not consume an attack or release other hands.

`tests/test_unarmed_integrations.py` contains independent numeric cases and transaction/replay tests; `tests/test_tactical.py` verifies the v1/v2 HTTP boundary. Both capability families remain partial. These integrations do **not** complete #176 or #108.

## Unarmed Wait reactions

A `WaitTrigger` declares either a weapon reaction or an unarmed one, never both and never
neither. `WaitTrigger.unarmed` fixes the whole attack in advance — action, skill, hands or
foot, target location, and an arm lock's grip — because B366 commits a Wait's reaction
before the trigger fires. A reaction is an ordinary Attack or an All-Out Attack, so
`reaction` cannot be Feint or Ready and a stop thrust cannot be declared unarmed. Only the
waiter's stable choices are checked at declaration: profile, skill support, a named foe,
free usable hands, standing posture for a kick, and the waiter's own two-hand grapple for
an arm lock. Reach, posture and the foe's state belong to the reaction and are validated
when it happens, so a Wait declared across the battlefield stays legal.

- Every turn-consuming unarmed action is the observable attack a Wait answers. Releasing a
  grip and applying arm-lock damage are free actions on the holder's turn, so they do not
  trigger one. As with an armed Move and Attack, an unarmed attack is an attack rather than
  a move, so a movement Wait zone does not fire on close-combat entry.
- Close-combat entry is applied before the pause, exactly as an armed step is, and is
  stripped from the saved command so the resumed turn cannot enter twice.
- The pause happens before any dice, before turn bookkeeping and before fatigue exertion,
  so the interrupted turn resumes whole. `ResumeInterruptedTurn` replays the stored unarmed
  command; cancelling it spends the turn doing nothing instead.
- The reaction borrows the interrupted turn rather than adding one: the waiter's injury
  turn does not advance again, and its attack/defense pause survives inside the interrupt.
  The waiter can decline with `do_nothing`; no other actor may act while the turn is paused.
- A reaction that departs from its declaration is rejected before dice, as is an armed
  reaction to an unarmed declaration, an unarmed reaction to a weapon declaration, and a
  stop thrust answering close-combat entry.
- A grappled or grappling fighter may commit to a Wait. Evaluate, Feint, Aim, Concentrate,
  movement, posture steps and reloading while a grip is held remain rejected as #176 work.
- The tactical projection offers unarmed Wait declarations per target and, once paused,
  offers the waiter exactly the declared `take_unarmed_turn` reaction and the decline.

Independent cases are in `tests/test_unarmed_wait.py`; `tests/test_tactical.py` covers the
hex projection and the v1/v2 HTTP boundary. Wait integration does not complete #176 or #108.

## Versioned command contract

`/api/tactical/v2/campaigns/{cid}/commands` accepts `TakeUnarmedTurn.maneuver` and `attack_option`, plus `ChooseDefense.parry_mode_id` and `second_parry_mode_id` and `TakeCombatTurn.wait_trigger.unarmed`. The unchanged snapshot format remains `tactical-v1`. Gameplay v1 and the tactical v1 input schema remain unchanged; the latter rejects the new options. Omitted v2 options do not alter the canonical command payload, preserving existing receipt digests. The tactical v1 request keeps its own frozen `TakeCombatTurn` and `WaitTrigger` shapes, which require `item_id` and reject `unarmed`; the v1 document gains only the unreferenced `UnarmedReaction` definition that the shared snapshot projection carries. See `contracts/tactical/v2/openapi.json` and `frontend/src/api/tactical-v2.generated.ts`.

Verify both contracts with `uv run python -m scripts.tactical_contracts --check` and `uv run python -m scripts.tactical_contracts --version 2 --check`. The exact historical printing/errata equivalence audit remains open; numeric tests are not a source-baseline certification.
