# Armoury operating toolkit admission — #977

This bounded implementation uses the selected Characters third printing B178
and B289, and Campaigns fourth printing B345–346 and B483–485. Those rules
supply Armoury specialties, equipment modifiers and repair timing/consequences.
They do not supply a universal powered-tool fuel rate. Canonical portable
Armoury kits retain their existing declarations.

A newly captured `repair-operating-tools` combat feature refuses unselected
operating tool combinations before parts or success-roll entropy, including
required kits whose generic or mismatched skill features previously escaped the
exact-skill modifier lookup. Fresh admission classifies all declared operating limits first, and accepts
only the single plain exact-skill internal-seconds contract below. Capacity/count
variants remain unsupported rather than being converted to seconds.
A required-definition identity cannot bypass
operating constraints supplied by a non-tool feature, electronics suite or
energy-cell profile; those combinations remain unsupported by this contract.
Recorded commands without that feature retain
their historical semantics. A fresh finish or cancel does not retrofit the
start of an old pending task.

Private Armoury planners independently capture strict
`operating_tools_generation` metadata. Current eligibility checks therefore
reject unsupported operating combinations before a parts-assessment die or
work-selection commitment. Historical absent metadata keeps the old planning
receipts, outputs and seed replay. The new private wrapper retains the exact
original public input; validated intent comparison removes only this metadata,
rejecting whitespace/intent changes, malformed generations and duplicate
private keys. This is consistency of this bounded host,
not a claim that B485 imposes a printed powered-tool prerequisite on every
independent inspection. Proposed work methods use the canonical timing table
in pure eligibility, including combined default/time selection: a lawful
900-second haste proposal fits900 seconds and refuses899, while extra time
requires its actual longer budget. No temporary persisted time plan is forged.

The supported new operating contract requires an explicit trusted GM tool
observation and player selection, one exact repair-skill tool feature, its
pinned operating duration, and one explicit internal reservoir measured in
active seconds. These are the existing `GeneralEquipmentFeature` and
`UseGeneralEquipment` charge units; they do not establish a licensed source
fuel rate. Energy-cell, electronics, multiple-feature and external-consumable
conversions remain unsupported. New explicit seconds reservoirs are validated
against their pinned duration; historical omitted tool charges are not filled
in or silently migrated.

The actual selected work duration must fit the current reservoir and pinned
limit. Start reserves that budget without spending future time. Finish spends
the interval once on success or failure. Cancel spends elapsed time, capped at
the admitted duration; an actual recorded disabling object result ends active
time earlier. Only disable events after the authenticated producer count; an
older same-tick receipt cannot stop a newly started operation. Immediate
cancellation spends zero. Existing custody commands,
general use (including use as another item's fuel) and artifact operations
cannot spend or reassign the reserved kit.

Private producer records bind the start command, actor, damaged object, physical
kit, profile digest, start time, duration and original reservoir. Strict numeric
fields and authenticated event identity reject malformed or duplicate records.
Authored genesis cannot seed them. Player campaign and stream projections omit
the private records. Existing public command/task shapes and old input bytes
remain unchanged.

Finish retains current custody, observation, profile and health checks. Cancel
retains its earlier escape path: it settles the authenticated old reservation
against the same physical reservoir without requiring current approval,
health, ownership or a current profile digest. An actual GM price-only catalog
migration therefore cannot strand cancellation. Missing/replaced reservoirs
remain an unsupported migration; their units are never invented or refunded.

Regression coverage includes SQLite and PostgreSQL armor/firearm repair HP and
disabled consequences, parts conservation, exact retries, stale/unauthorized
refusal, insufficient budgets before entropy, success/failure, cancellation at
0/100/1800 seconds, B346 haste50 (900 seconds and −5), original-genesis seed
reexecution and both directions of producer-policy preservation. Actual
price-only migrations verify finish refusal, cancel settlement, exact retry and
store folding. The generic replay adapter does not support whole rules-migration
seed reexecution; no such claim follows from those cases.

Lost-health/approval/custody cancellation and native object destruction have
separate pure continuation diagnostics. They do not certify a new persisted
unheld-tool destruction host. This slice does not close #977 or promote full
Armoury compliance: missing tools, broader alternatives, external consumables,
unit conversions, unsupported migrations and complete work-interruption
modeling retain their explicit remaining scope.
