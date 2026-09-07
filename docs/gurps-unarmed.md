# Basic Set unarmed combat and grapple control

Issue #108 adds internal commands to the existing `CombatService` transaction. The selected profile must be exactly `gurps-basic-set-4e-2004`, and both participants must have explicit living-human anatomy. Existing campaign pins and square coordinates retain their meaning. The hex geometry contracts from #105 remain separate; #115 owns the versioned map/API/UI integration.

## Supported commands

| Command/action | Behavior |
| --- | --- |
| `TakeUnarmedTurn`: `punch`, `kick` | Compiled DX or selected striking skill, thrust-based crushing damage and per-die training bonus. Punches require a named free hand; kicks name a foot. A missed kick checks balance. Armor and resulting wounds use the existing injury service, including injury to a bare striking limb against DR 3+. |
| `grapple` | Explicit one/two-hand control of torso, neck, arm or leg; reach C and explicit entry into the target's square. No damage on initiation. Torso control imposes the DX-related attack/defense penalties; a controlled arm cannot strike or parry. |
| `ChooseDefense` | The existing authenticated command resolves an unarmed pause. Supported choices are Dodge, barehanded Parry and no defense. All-Out Defense (Double) permits an ordered fallback using a different defense or a different free parrying hand. Both choices are validated before dice; the fallback rolls only after an ordinary failure. Ordered choices and actual checks survive receipt replay. No other combat command can bypass the pause. |
| `break_free` | One Quick Contest with grip, pin, stun and lock modifiers. A failed arm-lock escape makes subsequent attempts harder. Pin escape attempts have a ten-round interval. |
| `takedown` | One Quick Contest using ST, DX or grappling skill against a standing opponent. The loser falls and loses the reciprocal grip. |
| `pin` | One Regular Contest round, using the existing contest normalization. The free-hand advantage is included. Both-success/both-failure leaves control unchanged and requires another turn, without rolling ahead in time. |
| `arm_lock` | Offensive path from a surviving, two-hand Judo/Wrestling grapple on an earlier turn; an attack/defense pause captures the selected arm. |
| `lock_damage` | Once on each subsequent holder turn, a passive contest applies crushing damage to the arm, excluding flexible armor. The action does not consume the holder's attack. Already-crippled-arm pain effects remain unsupported. |
| `strangle` | A neck-grip contest applies crushing neck damage. Penetrating injury starts the existing durable suffocation schedule; one-hand use carries its penalty. |
| `ResolveChokeEffects` | The victim settles a due grip-specific suffocation deadline. Existing hazard/fatigue logic owns FP, consciousness and the no-air deadline. It consumes no combat turn and cannot duplicate a tick. |
| `release` | Free release on the holder's turn. Releasing a choking grip ends its hazard after due effects are settled. |

The suffocation adapter uses the existing one-second shared combat clock. It does not introduce a second clock or a player-authored damage parameter. Individual-actor phase timing for choking, alongside other tactical timing refinements, remains part of #176.

## Persistence and authority

Grips, hand commitments, close-combat relationships, arm-lock escape penalties, pin deadlines, unarmed defense intent and check traces persist in `Encounter`. Grip ownership and distinct-hand invariants are validated on reload. HP, FP and suffocation use the existing resource state and receipts. A third-party knockout or disabling injury to the holder releases unusable grips, while an unconscious target can remain held. Ending an encounter does not silently cure a choking victim.

Commands use the existing authenticated actor check, canonical payload digest, campaign revision/CAS and committed result replay. A different payload cannot reuse a command ID. Recovery, ability and party transitions recognize the unarmed pause. Noncombat actions already reject actors in an active encounter. No frozen v1 schema is changed and no new LLM capability is silently enabled.

## Evidence and limits

The declared source is Basic Set Fourth Edition, first printing (2004), with the January 26, 2007 first-printing errata baseline. Numeric references: Characters B182, B203, B228 and B271; Campaigns B349, B370-371, B379, B400, B403 and B436. Tests contain numeric expectations and references, not rulebook prose. Independent cases are in the common conformance ledger and `tests/test_unarmed.py`. The exact printing/errata artifact audit remains outstanding; the implementation is not a conformance certificate.

Both `gurps.combat.unarmed` and `gurps.combat.grappling` remain **partial**, which keeps the existing scenario/character capability checks fail-closed. #108 remains open. [Follow-up #176](https://github.com/Underzenith85/wayfarer/issues/176) tracks the remaining work:

- Unarmed critical tables and critical-defense consequences. Table dice are recorded and the encounter blocks, rather than substituting the armed critical-miss table or ordinary damage.
- Armed parries versus bare limbs, skill-specific advanced defenses, Wait/resume, attack options, evaluation/feint bonuses, retreat and tactical hex integration.
- The defensive parry-to-arm-lock route, pain on an already crippled locked limb and the distinct Choke Hold technique.
- Free-hand Ready, partial hand release, escape steps, dragging/carrying, twice-ST movement exceptions, Size Modifier/multiarm variants and additional strikes/targets. Unsupported movement/Ready/maneuver combinations are rejected explicitly.

Current bodies have no authored Size Modifier, so tests cover equal-sized human participants. This does not implement large/small creature grappling. Optional/supplement grappling systems and control points are excluded.

The Double Defense subset of #176 has restart, duplicate-receipt, pre-dice rejection, distinct-hand, fallback ordering and critical-blocker regression tests in `tests/test_unarmed_double_defense.py`. Critical outcomes still preserve table dice and block continuation, including when reached through the fallback. This subset does not complete #176 or the source-baseline audit.
