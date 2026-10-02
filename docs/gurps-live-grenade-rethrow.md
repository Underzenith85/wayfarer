# Delayed grenade recovery and rethrow

Printed Basic Set Campaigns B373 treats a thrown grenade as a ranged attack and
requires recovery or another readied weapon after the throw. B410 explicitly
allows an enemy to kneel, ready a nearby delayed grenade, and throw it back,
spending one second on each maneuver. The engine already provides those three
canonical combat commands, including witnessed landing, free-hand, posture,
current ownership and due-blast checks.

A rethrow moves the same armed item. Its original explosive payload, blast cause
and fuse deadline must survive: throwing it again neither manufactures a second
warhead nor restarts the fuse. A missed area throw still records the ordinary
B414 scatter and current ground position for that item. The original blast follows
the current source instance when its deadline arrives.

This route does not authorize grenade catching. The canonical grenade mode
rejects `catchable`; the general critical barehand catch rule does not establish
an independent grenade-catching command. B415 airbursts require posture-independent
fragment exposure and overhead cover, but the existing authored explosion shape
has neither altitude nor an overhead-cover distinction. Those boundaries remain
unsupported. This work is a bounded consumer, not complete acceptance of issue869.

The corrected scheduling rule is enabled by the trusted private combat-input
feature `grenade-fuse`. The public combat command remains unchanged. Existing
accepted inputs without that feature retain their original scheduling behavior,
including the former duplicate blast records on rethrow; event folding and seeded
reexecution must agree with those accepted facts. New inputs preserve one original
cause and deadline, including after restart and exact retry. This does not add a
new fragment Luck launch identity for the later thrower; the original immutable
source receipt remains the fragment host's source boundary.
