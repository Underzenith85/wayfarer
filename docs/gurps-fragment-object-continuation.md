# Object consequences during pending fragment attacks

Characters third-printing B66 permits the defender's Luck to select the worst
opponent attack roll. Campaigns fourth-printing B414 identifies blast damage as
incendiary; Characters B136 and Campaigns B484 apply the existing combustible
object rules to that damage. A new fragment preparation now preserves this
property when its unresolved object blast packets reach the object reducer.
Ordinary cutting fragment packets do not receive the blast's incendiary flag.

The durable phase retains each object's identity and exposure position, then uses
its current condition and custody when applying unresolved damage. The source
explosive remains one permanently spent instance after completion. Tests in
`tests/test_fragment_object_custody.py` prove a transferred, previously damaged
spent source goes from 9 HP to 3 HP after a six-point blast, ignites on its failed
HT 10 check, retains its new owner, and is not duplicated or re-equipped. A
one-point blast followed by a six-point cutting fragment instead inflicts nine
points of fragment injury without an incendiary HT check. Prior actor injury and
the accepted launch remain unchanged after source approval is revoked.

New private host inputs and cursors record the object-generation choice and
canonicalize declaration maps for deterministic preparation replay. Inputs and
cursors without that marker keep their earlier behavior. SQLite and PostgreSQL
fixtures cover new and historical canonical-order inputs, restart, exact retry,
authority and stale-revision refusal, and seeded preparation and completion.
Historical object inputs whose original map order was lost during canonical JSON
serialization remain an inherited re-execution limitation; retained event folds
and existing cursors are not rewritten.

This boundary does not complete #869. It does not implement burning ticks for
spent objects, secondary explosions from volatile objects, incidental or hot
fragmentation, or airburst posture rules. Catching a thrown weapon on a critical
bare-handed parry is described at B381, but canonical grenade construction still
rejects catchable modes and due-blast guards prohibit unrelated activity. This
change introduces no grenade catch, recovery, or rethrow procedure.
