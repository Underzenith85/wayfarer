# Combat maneuver lifecycle

Issue #104 adds profile-selected commitments to the existing CombatService and
encounter receipts. Prototype campaigns retain their original maneuver set.
The numeric expectations are provisionally aligned with Basic Set B363-366,
Fourth Edition (2004); available Campaigns fourth-printing text is cross-checked
without claiming verification of the frozen first-printing/errata baseline.

| Maneuver | Executable behavior |
| --- | --- |
| Aim | Ready ranged mode and target, Acc plus one/two extra seconds, separately typed bracing and fixed/variable scope bonuses capped by base Acc, defense spoils aim, injury requires Will; ranged attack consumption belongs to #106 |
| Evaluate | Consecutive +1 bonuses capped at +3, target bound, next maneuver expiration |
| Feint | Recorded opposed skill rolls; successful margin reduces only this actor's next attack defense |
| All-Out Attack | Determined +4, ST-based Strong +2 or +1/die, same- or two-weapon Double with separately receipted defenses and the ordinary off-hand -4 unless Ambidexterity applies, same-turn Feint; no active defenses |
| All-Out Defense | +2 to chosen defense, or two distinct legal defenses with the second rolled only after failure |
| Move and Attack | Full movement, melee -4 capped at 9, no parry |
| Concentrate | Consecutive commitment, Will-3 disruption on defense/injury; ability execution retains its existing service |
| Wait | Typed observable actor/target and hex-zone conditions, exact reaction, persisted pause, one-shot reaction, authenticated resume/cancel, and no duplicate movement, round, or turn-start processing |
| Stop thrust | A ready thrusting mode may interrupt a declared foe moving at least one yard toward the waiter to attack; longer reach strikes first and adds +1 thrust damage per two full yards moved |
| Existing maneuvers | One movement allowance; explicit before/after Attack steps, step/facing and standing-kneeling step options, explicit prone-to-kneeling-to-standing transitions |

The authenticated ability path respects interrupted Wait and forced Do Nothing;
selecting Concentrate clears prior maneuver bonuses. Injury, recovery deadlines,
and fatigue remain authoritative. Invalid parameters are checked before voluntary
turn-start dice. `forced_do_nothing` supplies the combat consequence seam for #107.

Issue #152's bounded follow-up scope is implemented through the same command/CAS
boundary and exposed as server-filtered tactical choices. Unsupported combinations
still fail before voluntary turn-start dice: non-thrust stop attacks, unobservable or
off-map zones, equal/short-reach stop-thrust ordering, non-Attack deferred steps,
invalid hand occupancy, and catalog modes without declared bracing/sight support.
Broader ranged variants remain #173, and source-inventory reconciliation remains
#191; this page does not claim whole-profile certification.

`tests/test_gurps_maneuvers.py` and `tests/test_maneuver_followups.py` check independent targets/damage, bonus lifetime,
defense prohibition, multiple attack timing, Aim, concentration, movement legality,
scope/bracing timing, two-weapon penalties, deferred steps, zone entry, stop-thrust
damage, and SQLite restart/retry of interrupted actions. Existing melee and ability
tests cover the shared causal guards. Numeric expectations are independently
cross-checked against Basic Set Fourth Edition B364-366, B385, B390, B412, and B417.
