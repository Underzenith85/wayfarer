# Acrobatic Dodge trait bonuses (#878)

Basic Set Characters third printing B174 grants Perfect Balance +1 to
Acrobatics, Aerobatics and Aquabatics. B74 gives its Acrobatics bonus; B34 and
B174 grant 3D Spatial Sense +2 to Aerobatics. Campaigns fourth printing B375
uses Acrobatics for the preceding Acrobatic Dodge check, substituting Aerobatics
in flight. These are purchased task bonuses, distinct from the temporary DX
reductions exempted for this reaction by B419/B421.

The current approved defender build supplies the purchases. Perfect Balance
adds +1 to either supported preceding skill; 3D Spatial Sense adds +2 only to
Aerobatics. Their independent bonuses stack. They adjust the recorded skill
trace before its success/failure becomes +2/-2 on the actual Dodge. They do not
add directly to Dodge, create a maneuver or consume another turn.

Previously, a defender with skill 8 and Perfect Balance failed a roll of 9,
receiving Dodge -2 and actual injury. Under the corrected private generation,
that roll succeeds against 9, giving Dodge +2 and preventing injury when the
Dodge roll is 9. Flying source oracles cover each bonus and their +3 sum.
Ground 3D Spatial Sense and absent purchases grant no bonus.

The trusted private combat feature `acrobatic-trait-bonuses` controls this
change. The integrated fresh feature set enables it. Recorded commands with
absent features or only the grenade feature keep their old checks and injury. Both
stores verify event folding, seed-only reexecution and exact receipt retry
after the fresh feature set changes. Public commands and schemas are unchanged.

This bounded consumer does not close #878 or certify all uses of these traits.
Aquabatics selection, aerial/swimming concealment steps, specialized
interpositions and other trait applications retain their separate source and
acceptance boundaries. Modified or conditional trait applicability requires
its own supported activation consumer; this lane adds no such policy.
