# Combat maneuver lifecycle

Issue #104 adds profile-selected commitments to the existing CombatService and
encounter receipts. Prototype campaigns retain their original maneuver set.
The numeric expectations are provisionally aligned with Basic Set B363-366,
Fourth Edition (2004); available Campaigns fourth-printing text is cross-checked
without claiming verification of the frozen first-printing/errata baseline.

| Maneuver | Executable behavior |
| --- | --- |
| Aim | Ready ranged mode and target, Acc plus one/two extra seconds, defense spoils aim, injury requires Will; ranged attack consumption belongs to #106 |
| Evaluate | Consecutive +1 bonuses capped at +3, target bound, next maneuver expiration |
| Feint | Recorded opposed skill rolls; successful margin reduces only this actor's next attack defense |
| All-Out Attack | Determined +4, ST-based Strong +2 or +1/die, same-weapon Double with separately receipted defenses, same-turn Feint; no active defenses |
| All-Out Defense | +2 to chosen defense, or two distinct legal defenses with the second rolled only after failure |
| Move and Attack | Full movement, melee -4 capped at 9, no parry |
| Concentrate | Consecutive commitment, Will-3 disruption on defense/injury; ability execution retains its existing service |
| Wait | Typed observed actor attack/move declaration and exact reaction, persisted pause, one-shot reaction, authenticated resume/cancel, no duplicate round or turn-start processing |
| Existing maneuvers | One movement allowance; step/facing and standing-kneeling step options, explicit prone-to-kneeling-to-standing transitions |

The authenticated ability path respects interrupted Wait and forced Do Nothing;
selecting Concentrate clears prior maneuver bonuses. Injury, recovery deadlines,
and fatigue remain authoritative. Invalid parameters are checked before voluntary
turn-start dice. `forced_do_nothing` supplies the combat consequence seam for #107.

Coverage remains **partial**. Arbitrary Wait zones/reflex conditions, stop thrust,
two-weapon/off-hand Double choices, bracing/sights, and attack-then-step timing are
not exposed by this bounded command model. They are tracked in #152;
full ranged resolution is #106 and tactical UI exposure is #115. Certification
must not treat typed hooks or the tests as evidence that these omissions work.

`tests/test_gurps_maneuvers.py` checks independent targets/damage, bonus lifetime,
defense prohibition, multiple attack timing, Aim, concentration, movement legality,
and SQLite restart/retry of an interrupted Wait. Existing melee and ability tests
cover the shared causal guards.
