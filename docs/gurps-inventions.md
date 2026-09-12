# Ordinary invention projects

Wayfarer implements the ordinary invention lifecycle from the selected fourth-printing
*Campaigns*, pp. B473-B475. Rulebook wording is not reproduced here.

An authored `InventionBlueprint` pins the proposed concept, invention and related skills,
operation skill, complexity, novelty context, native and inventor TL, facilities, funding
pool, materials, work durations, production target, and optional catalog/runtime bindings.
The ordinary method rejects devices more than one TL ahead; cinematic gadgeteering remains
a separate, unsupported adapter.

`InventionProject` persists monotonic concept/design, prototype, testing, production, and
complete phases. Work begins by checking actor availability, skills, facilities, funding,
and all materials before changing the project clock. Required money and inventory are then
consumed once and recorded on the active work item. Settlement is unavailable before the
shared-clock deadline and records its check, failures, flawed theory, facility accident,
and discovered or cleared bugs. Production records lots and costs but does not create an
inventory device. A lot exposes behavior only when both an implemented catalog definition
and a runtime adapter were authored.

Every create, begin, settle, and abandon command has a payload digest, resource receipt,
and serialized outcome event. Replaying a command after JSON reload returns that outcome
without spending again or consuming entropy. Active invention work blocks incompatible
immediate and queued full-time activity through the campaign activity guard.

Source-derived constants retained by the implementation include the ordinary complexity
penalties, the one-TL penalty and ceiling, the operation-skill testing penalty, and the
prototype bug bands. Campaign-specific prices, facilities, materials, and elapsed work are
explicit authored data rather than hidden defaults.
