# Held hand spells with Boxing and Karate punches

Characters third printing (February 2008), B240 **Melee Spells**, uses DX or an
unarmed combat skill for the hand attack. B182 **Boxing** and B203 **Karate** both
explicitly support punches. Their physical damage bonuses remain the ordinary
striking bonuses: Boxing grants +1 per die at DX+1 and +2 at DX+2; Karate grants
+1 at DX and +2 at DX+1. The independent B245 Deathtouch packet remains the
selected 1–3d regardless of that physical training bonus.

A fresh charged-hand declaration may now select genuinely purchased Boxing or
Karate for one ordinary punch using the exact empty, usable charging hand. The
current approved build must contain the admitted skill level and its canonical
attribute, difficulty, defaults and combat procedure. Campaign acquisition gates,
including Unusual Background prerequisites, remain the compiler's responsibility;
they cannot substitute altered skill mathematics. The source charge's build,
body, hand and mana witnesses retain their existing current-state checks.

The canonical unarmed declaration, attack, defense and physical damage reducers
are reused. DX10/Boxing12 or DX10/Karate11 gives +2 per physical die. With ST10 and
an actual physical roll of3, the punch inflicts2 crushing injury. Leather torso
DR2 stops that packet, while three separately rolled Deathtouch ones still cause3
magical injury. Karate's ordinary encumbrance penalty affects its attack check;
Boxing retains its own consumer. Neither changes the spell dice or repeats the
casting cost.

Actual Dodge and weapon Parry preserve the held charge; a successful barehand
Parry prevents the punch but admits the armor-ignoring magical arc. Existing close
combat Block restrictions are retained, including refusal with a real shield.
Kicks, multiple attacks/defenses, hand-touch aliases, Staff wand delivery and
joint-grip wrench remain outside this expansion. Passive Parry with a charged
hand does not become an attack or discharge through the new admission gate.

The private `hand-melee-striking-skills` declaration feature captures exact-integer
`striking_generation: 1` on the actual Boxing/Karate contact. Settlement revalidates
that record even if the response has no new feature. Generation-one DX/Brawling
contacts retain the absent field and their original serialization/behavior. The
contact's existing immutable digest covers the added marker, and player/spectator
campaign and stream projections omit it. No public schema or certification status
is promoted.

`tests/test_hand_melee_striking_skills.py` checks actual paid casting, punches,
physical/magical HP and armor, current source admission, legacy refusal and old
Brawling replay, exact retry/restart, authority/stale refusal, rollback, defense
implement classification, Karate encumbrance, typed owner-damage settlement and
original-genesis seeded reexecution in SQLite and PostgreSQL.
