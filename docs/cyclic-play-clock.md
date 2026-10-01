# Persisted Cyclic clock integration

The private approved-attack host advances new source-bound occurrences through
full campaign state. The resource reducer still owns scheduled damage and dice;
the play clock checkpoints the current actor/body after each occurrence, including
coincident deadlines. The selected sources are Characters third printing B55, B61–62, B83–84 and B103–104, and
Campaigns fourth printing B363, B421 and B443.

Ordinary actions, combat round settlement, party barriers, scene travel, physical
feats, noncombat approaches, timed social work, spell turns, lock operations,
enchantment work and harmful-physiology advancement preserve the returned full
state. A form reverting after unconsciousness changes the body used by the next
Cyclic tick. New injury drops retain item ownership, record actual ground
location and clear current ready/hand projections. Grip, choke, incapacitation
and combat-end crippling settlement use the same reducers as ordinary combat.

The outer transaction remains one revision. Internal clock identities are
bounded and deterministic; the original final Advance receipt retains its
original payload identity. Party advancement preserves its existing ordering of
schedules, cross-scene effects, activities and NPC work. A resource-only caller
without current approved actor context rejects a new bound deadline rather than
using stale captured HT or armor. Campaign procedures that still expose only a
resource callback retain that explicit limitation; the ordinary play and party
clocks remain available. Old occurrences without a private source binding retain
their recorded resource-only behavior and historical replay fixtures.

The B556 loss-of-balance restriction also reaches voluntary spell, ability,
equipment, movement, scene and noncombat actions. Active defenses and compulsory
resolution remain available. A pure time-only Wait or Do Nothing permits play to
continue; once combat has ended, B363's elapsed second supplies the next-turn
boundary. The original critical roll and any GM policy remain private evidence.

Independent integration evidence lives in `test_cyclic_play_clock.py` and
`test_innate_host_action_guards.py`. The form case starts at 10 HP, takes 6 damage,
returns unconscious to a 20-HP native body with 8 HP, then takes 2 more damage and
ends at 6 HP. Both different-time and coincident-deadline cases use this oracle.
The original final clock receipt is retained only after all same-time occurrences
settle; NPC activity stays after the resource consequences for that timestamp.
Other cases exercise actual SQLite/PostgreSQL services, seed-only
reexecution, stale commands, exact retries, private-prefix genesis rejection,
canonical held-item loss, and ordinary spell/ability eligibility after the
balance restriction expires. This evidence does not certify unsupported damage
variants, private GM substitutions or the entire engine.
