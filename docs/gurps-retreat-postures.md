# Retreat posture admission (#878)

The supplied selected Campaigns fourth printing B377 forbids retreat while
sitting or kneeling and explicitly permits a lying fighter to roll away. The
B551 posture penalties still apply to the defense; permission to retreat does
not remove a posture penalty. Standing retreat retains its ordinary bonus.

The correction applies only to the exact Basic Set equipment profile. Mapless
combat also serves Lite and prototype rules; those retain their existing
retreat admission even when the private feature is present. Hex battlefields
are already bound to the exact Basic Set profile, and migration validation
rejects a different equipment profile. Real Lite and Basic Set mapless sitting
retreat cases, with the feature present and absent, are covered on both stores
in `tests/test_defense_posture_profiles.py`.

The previous hex kernel refused lying and allowed kneeling. The mapless Basic
host refused kneeling but omitted sitting. The private `retreat-postures`
feature now selects the source posture table in actual Basic and hex defense
admission and in both hex-to-Basic feasibility derivation and Basic-to-hex
consistency checks. The pure geometry helper defaults to the source table;
historical hosts explicitly select their captured former policy. Recorded
absence therefore preserves each representation's original acceptance/refusal,
not a new common interpretation of old commands.

Actual source oracles start with approved ordinary builds and change the
defender's posture through combat commands. Base Dodge 9 becomes 6 while prone;
a rolling retreat adds 3, yielding target 9. A roll of 8 avoids injury. A roll of
10 fails, suffers 3 HP injury, and still completes the retreat while remaining
prone. Standing retreat checks against 12. Kneeling/sitting refuse before dice,
movement or revision changes. A rejected kneeling retreat cannot leak its bonus
into a later ordinary Dodge: that target stays 7, and a roll of 8 causes 3 HP
injury. The separate [shared defense-posture consumer](gurps-defense-postures.md) now
applies the missing ordinary sitting/crawling penalties through independent
per-command capture.

`tests/test_retreat_postures.py` and `tests/test_retreat_posture_replay.py` cover
authority, stale revision, rollback, actual defenses/injury and movement,
restarted exact retries, current feasibility, real Basic/hex migration
roundtrips, old hex prone refusal and old erroneous seated/kneeling acceptance.
Direct and typed opponent-defense commands reexecute from persisted
pre-response snapshots using server seeds after fresh active features change.
Both SQLite and PostgreSQL are exercised. These proofs do not claim original
genesis reexecution of every posture-producing campaign.

Current Basic/hex legal-choice previews explicitly select fresh command
semantics, so they offer rolling retreat and omit seated/kneeling retreat just
as the actual command does. These queries consume no dice and change no state.
Square-map defenses still lack an admitted retreat destination contract and
retain their existing refusal. No public schema or mapless terrain-authority
contract changes. Shock, stun, retained grips, maneuver restrictions, equipment,
retreat distance and other once-per-turn rules retain their existing separate
consumers. Flight concealment and its missing producer/anchor/visibility joins
remain outside this correction. No source-ledger or broad certification status
is promoted.
