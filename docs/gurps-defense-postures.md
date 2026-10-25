# Shared active-defense posture penalties

The selected Campaigns fourth printing B549 and B551 give the same active-defense
table: standing/crouching 0, kneeling/sitting −2, crawling/lying −3. These
penalties apply to Dodge, Block and Parry. Permission to retreat is a separate
B377 rule; neither permission nor a retreat bonus removes the posture penalty.

The two canonical scoring sites previously handled only kneeling and prone,
omitting sitting and crawling. Both now use the shared pure
`posture_defense_modifier` table. This covers ordinary Dodge, shield Block,
weapon Parry and the supported unarmed-Parry route. Attack posture modifiers,
body footprints, ranged target concealment and ordinary weapon-to-barehand
Parry admission retain their separate contracts. The melee attack table and
its producer policy are covered in [Melee attack postures](gurps-melee-postures.md).
In particular, these tests do
not manufacture a held spell contact to bypass an unsupported parry carrier.

The independent private `defense_posture_generation: 1` field accompanies the
existing command metadata envelope. The Symptoms version and its exact original
caller bytes remain independent. The common pipeline captures this defense
version and supplies `RulesContext.correct_defense_postures` while assessing and
resolving a command. This matters because composed attacks and spell hosts also
use the scorers outside the ordinary combat feature context. Recorded absence
selects the former scoring in all hosts, including before a replay plan builds
its runtime context. Exact retries retain the recorded field and input identity.
No public command, player-supplied modifier or accepted generation selector is
added.

Generation 1 selects this source policy only for the exact
`gurps-basic-set-4e-2004` equipment profile. The shared defense scorers also
serve Lite; Lite retains its existing posture scoring even when the private
generation field is present. The unavailable Lite artifact is not evidence for
borrowing Basic Set rules. Prototype combat does not enter these GURPS scorers.
`tests/test_defense_posture_profiles.py` exercises real Lite and Basic Set
Change Posture, defense outcomes and exact retries on both stores, with the
generation both present and absent.

Tests create sitting/crawling postures through actual Change Posture commands,
and crouching through its actual free-posture command. Ordinary DX10 builds
exercise the full source table on actual Dodge, Block and weapon Parry. A real
left-hand punch supplies the unarmed DX-Parry consumer. Base Dodge9 falls to7
while sitting and6 while crawling. The untrained barehand Parry8 falls to6 or5.
Actual source-positive damage packets distinguish successful defense from the
corrected failures; authority, stale revision, rollback, target HP and exact
retry are checked. Basic and hex producers join the same table, while the
ordinary square host also exercises all supported defense branches.

The actual composed-attack host now records the independent generation despite
having no ordinary combat envelope. Actual Fireball release and subsequent
Dodge exercise the spell carrier. Push has no orchestration host in this tree;
its existing real reducer consumes the explicit runtime flag, with real authored
posture and resulting knockback tested. That reducer test does not claim a
missing service or endpoint exists.

Evidence: `tests/test_defense_postures.py`,
`tests/test_defense_posture_replay.py`, `tests/test_defense_posture_hosts.py` and
`tests/test_defense_posture_metadata.py`. SQLite and PostgreSQL host tests cover
old/current direct and typed opponent-defense reexecution from persisted
pre-response snapshots, after fresh generation settings change. Strict metadata
tests preserve exact bytes across object, opaque and array payloads, reject bad
versions/digests and altered originals, and retain the first canonical legacy
encoding. The owner-damage staging proof retains an already-failed posture defense while
the later damage-acceptance command uses a different generation; both recorded
commands reexecute without rescoring that historical defense. No whole-source
certification status is promoted.
