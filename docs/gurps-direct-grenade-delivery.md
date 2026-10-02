# Direct owner-damage task grenade delivery

Characters B66 permits Luck against an opponent's attack roll. Campaigns B410
permits a witnessed enemy grenade pickup and rethrow after the required maneuvers.
The direct `PrepareOwnerDamage` host accepts the target's actual ordinary defense
and executes that canonical inventory delivery before considering a damage pause.
A delayed grenade schedules its payload and completes this route without a new
owner-damage pending roll.

New direct preparations with an actual `ChooseDefense` capture the existing
private task grenade-fuse generation. The actual task continuation runs in that
captured scope; no public command fields or global combat features change.
Historical absent generations retain their original behavior, including two armed
causes after a rethrow. New delivery relocates one original cause without changing
its identity, payload, fuse dice or deadline.

Fragment provenance admits the full typed direct preparation only with canonical,
digest-verified task metadata, matching receipt command and principal identities,
and the actual pending defense target. The original grenade birth receipt and
specific canonical relocation event remain required. Clients cannot provide the
private generation. Exact public retries recover recorded metadata; altered public
bytes fail persistence identity.

Both-store actual host tests cover witnessed recovery, original fuse and current
custody, wrong authority and stale revisions, candidate rollback, restart and exact
public receipt retries, absent-generation and fresh seeded reexecution, rehashed
receipt ID/principal/actor/encounter rejection, and subsequent latest-thrower
fragment Luck with seeded preparation and consequence replay.

This adds the direct preparation as an executable delivery producer. Catch,
airburst geometry, hot/incidental fragments, volatile secondary explosions and
spent-object burning remain outside this change. Issue #869 remains open.
