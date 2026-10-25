# Karate Kicking in actual combat

The existing reviewed B232 metadata defines Kicking (Karate) as a Hard technique
at Karate−2, capped at Karate. At Karate 10, its default is 8; two points give 9
and three points give 10. The private `karate-kicking-technique` combat feature
now makes those compiled levels supply the actual Karate kick attack target.
Shock, posture, encumbrance, visibility and other ordinary attack modifiers still
apply separately. The parent Karate skill continues to determine striking damage
bonuses: buying a technique does not improve that underlying training.

Previously the unarmed consumer always rolled against the parent skill minus two,
even when the approved sheet contained an improved technique. It also ignored
an explicit campaign Unusual Background decision governing the technique's
availability. The canonical Kicking definition and its canonical Karate parent
are now checked before using this path. The exact prerequisites and status added
by the host's pinned B96 decision are permitted; altered parents, difficulty or
caps are unsupported and reject before dice.

A paid or forbidden decision naming `skill:kicking-karate` applies to that
specific technique. A missing admitted level prevents a Karate kick declaration,
including an unarmed Wait, and prevents fresh scoring from manufacturing its
default again. A permitted paid default requires the usual recorded GM approval.
The decision does not forbid a separate ordinary DX kick or another unselected
skill. Catalogs without the canonical Kicking definition retain the ordinary
parent−2 route.

Current approved builds supply fresh attacks. Existing captured attack sources
retain their original approved build, including background admission, through
the shared `actors.build` projection. Temporary consequences are still projected
at resolution. No parallel build compiler or point calculation was added.

Direct combat commands and task-produced unarmed attack/defense consumers capture
the private feature. Absent recorded features retain the original parent−2
validation and scoring, including exact retries and seeded command reexecution.
Public combat commands, stored effect fields and frozen transport schemas are
unchanged. This does not certify Arm Lock, other techniques, arbitrary modified
technique metadata or the entire unarmed family.

`tests/test_karate_kicking_technique.py` exercises real SQLite and PostgreSQL
combat, trained/default target values, actual HP loss, authority/stale rejection,
B96 admission, canonical metadata refusal, fallback, captured versus current build
semantics, retries, event folding and original-genesis seed replay.
