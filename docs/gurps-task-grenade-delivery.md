# Fragment Luck after an ordinary task-host grenade rethrow

Characters B66 permits Luck against an opponent's attack roll. Campaigns B373
uses the ordinary ranged attack route for a thrown grenade; B410 permits a
witnessed enemy pickup and rethrow after the required maneuvers. A task-host
opponent attack choice can complete that same declared throw through its actual
canonical defense response. That delivery must preserve the already armed
warhead and must identify the later thrower as the fragment opponent.

New ordinary `ChooseOpponentAttack` commands with an actual `ChooseDefense`
response capture a trusted private `task_combat_protocol_features` envelope field.
Only `grenade-fuse` is enabled here; the public command and global combat feature
set are unchanged. The feature scope surrounds the actual task continuation on
its campaign transaction. An original grenade cause is relocated instead of
creating another warhead or restarting its fuse.

The fragment binder requires the canonical birth receipt and the newly added
relocation event for the specific pending attack. A task-host relocation also
requires a digest-verified canonical task input, the captured feature, an ordinary
response, matching accepted command identity and matching recorded principal.
The original cause ID, source instance, encounter, payload, deadline, fuse dice,
deferred ticks and evidence remain intact. An unrelated receipt or an altered,
rehashed command/principal cannot establish a delivery.

Historical inputs without the task feature retain the previous two-cause rethrow
behavior and reexecute against their original generation. Public retries recover
the accepted private features even when current defaults change. Persistence
normalization removes only validated canonical private metadata; original public
bytes, principal, command and response remain the intent identity. Mixed feature
namespaces, unsupported routes, unknown/duplicate/non-list features and whitespace
changes are rejected. Clients cannot author the private field through TaskService.

Both-store fixtures execute the real enemy pickup, opponent task opening, ordinary
choice and subsequent fragment preparation/Luck choice. They cover current
custody, one original fuse, authorization, stale inputs, candidate rollback,
restart, public TaskService retry, direct public-byte duplicate lookup, seeded
historical and new task replay, and seeded fragment preparation plus choice replay.

This adds the ordinary opponent task choice as a producer. Separately staged
owner-damage producers do not capture this new task feature. Catch/rethrow rules
beyond the supported ground pickup, airburst geometry, hot/incidental fragments,
volatile secondary explosions and spent-object burning remain outside this change.
Issue #869 remains open for its unproved criteria.
