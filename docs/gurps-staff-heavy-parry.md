# Heavy barehanded Parry of a charged Staff

Campaigns fourth printing (April 2008), B376 **Parrying Heavy Weapons**, makes a
barehanded Parry fail automatically against a weapon heavier than the defender's
Basic Lift. The ordinary attack damages the defender, who is knocked back one
yard and makes a DX roll to avoid falling. This fixed yard does not depend on
B378's damage-based knockback calculation, and B376 names DX rather than the
highest of DX, Acrobatics and Judo. The failed attempted Parry consumes no Parry
roll or unarmed critical-failure table.

The actual held Deathtouch/Staff host now supports that consequence for a standing,
unrestrained ordinary human with positive HP and FP, an available hand and a
clear mapped destination. Its source admission uses the actual pinned incoming
weapon weight and current approved defender's Basic Lift. A four-pound
quarterstaff therefore exceeds ST4 Basic Lift of 3.2 pounds. The two physical and
magical injury packets still settle separately; the successful attack spends its
held spell before displacement. The resulting mapped position and, on failed DX,
both encounter posture and injury-state `prone` change together. Perfect Balance's
applicable B74 fall-avoidance bonus and current condition modifiers join the DX
check. B421 exempts this involuntary defensive reaction from temporary attribute
reductions: the actual current toxin-drowsiness regression retains DX10 rather
than ordinary DX8. Direct B361 aftermath penalties to attribute checks remain.
Incapacitation caused by the accepted impact does not prompt a new
voluntary balance roll.

The existing displacement reducer now exposes its placement/control/posture tail
for fixed-distance consequences. Ordinary B378 knockback retains its previous
damage threshold, choice of balance skill, optional Immovable Stance and dice
order. Heavy barehand admission validates its clear next step before response,
damage or fall dice. Mapless directions, unsupported square diagonals, occupied or
blocked destinations and map edges refuse atomically. Mounted/flying, grappling,
nonstanding and retreat compositions remain unsupported here.

The private `staff-heavy-bare-parry` response feature preserves historical
feature-absent refusal. Ordinary weight-qualified bare Parry still rolls normally;
missed attacks and critical hits permit no attempted heavy Parry knockback. The
private contact-family result records the admitted lift, incoming weight,
automatic failure, displacement and actual DX trace. It inherits held-spell
privacy and cannot be authored at genesis. Unselected damage-stage records retain
an absent `heavy_bare_parry` field.

`tests/test_staff_heavy_bare_parry.py` starts with mundane genesis, actually
manufactures Staff, casts a one-die Deathtouch, and resolves the real attack and
bare defense in SQLite and PostgreSQL. Scripted cases verify physical1 plus
magical1 injury, one yard of displacement, DX success/failure, exact entropy
consumption, authority/stale refusal, transaction rollback, restart retry after
feature changes and original-genesis seeded reexecution. It also checks legacy
and unsupported geometry refusal, ordinary lower-weight Parry, misses and critical
hits. This closes the bounded heavy barehanded-Parry refusal in the existing
Deathtouch Staff consumer; it does not certify the full Staff/contact capability.
