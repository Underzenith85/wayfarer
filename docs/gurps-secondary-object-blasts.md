# Secondary explosions from object damage

Characters B136 supplies Fragile (Explosive) destruction and explosion damage;
Campaigns B484 identifies eligible explosive artifacts. The existing object
reducer decides whether the actual wound explodes the object and emits its dice.
The blast packet consumer now schedules that authoritative result as a causal
child blast. It does not infer a new warhead, material, or fragmentation payload.

New captured `secondary-object-blasts` commands use the object's current ground
position or its current mapped inventory holder. The original parent's exposure
and attack identity stay fixed. A destroyed object stops receiving remaining
packets. A child can damage another explosive artifact and produce another child;
each child uses an immutable object-result command identity. The already detonating
primary carrier does not duplicate its own warhead.

If destruction consumes an artifact with another unresolved scheduled fuse, that
cause is fulfilled with a recorded supersession link. Its original payload and
fuse deadline remain in the record, and deferred clock work transfers to the
child. Resolving the chain releases that work once. Actual command retries return
the existing committed result and do not add another cause.

The private captured generation covers direct combat resolution and the existing
fragment task continuations. Recorded absence keeps historical packet behavior
and serialization. Public command schemas are unchanged.

Dedicated actual-host tests cover both SQLite and PostgreSQL, separate artifact
and chained shield explosions, independent damage numbers, original fuse
supersession, deferred round time, current holder position changes during a durable fragment pause,
owner/GM secret visibility, authority and stale rejection, failed checkpoint
rollback, restart, exact public retry identity, and seeded reexecution under both
old and new recorded generations. In the selected fragment example, accepting
three original hits leaves 6 HP while choosing the worst Luck miss leaves 9 HP;
the separately exposed artifact still generates the same authoritative child.

This is bounded downstream completeness for actual explosive object results.
It does not establish whole issue #869 acceptance or supply unsupported grenade
catching, airburst, incidental material fragments, or continuing incendiary damage.
