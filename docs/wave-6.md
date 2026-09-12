# Wave 6: character compilation and resources

## Character compiler (#8)

`wayfarer.engine.character.compiler.CharacterCompiler` is configured by the server with
an immutable rules package pin, campaign policy and trusted catalog effect
bindings. `compile()` accepts an untrusted draft containing a name, backstory and
purchases (`definition_id`, `amount`). Unknown fields, including totals, discounts
and effects, fail validation. Ordinary JSON arrays are accepted; booleans,
fractional values and numeric strings are not integer allocations.

Attribute amounts are purchased levels; skill amounts are points on the existing
original prototype curve; non-leveled traits have amount one. Point costs and
reduced-attribute disadvantage accounting come from the catalog. Manual and
unsupported definitions, parameterized constructions without a compiler,
prerequisite/exclusion failures and campaign-limit violations cannot activate.
This is declared prototype coverage, not a complete GURPS Fourth Edition compiler.

Results contain stable diagnostic codes with field paths. Legal compilation
produces an immutable purchased build, a separate derived sheet with effect
provenance and a content-derived revision that includes rules/policy pins.
Attribute effects feed skill levels before skill effects and ceiling checks.
Backstory is retained but supplies no mechanics. `dry_run=True` runs the same
checks without issuing a build. Duplicate-removal repair proposals are returned
only if the complete candidate passes dry-run validation. `activate()` recompiles
the draft and returns the build plus separate initial HP/FP runtime state; Wave 7
requires a trusted power-review authorization callback before activation; it does
not accept a client-provided build as authority. Campaign power approval is implemented by [Wave 7](wave-7.md); revision
history/advancement remains #18.

## Inventory and game time (#12)

`ResourceEngine` resolves immutable discriminated commands: transfer (including
stack splits), consume, equip, unequip, schedule and advance. Items have stable
IDs, quantities, owners, optional containers and equipment/readiness state.
Server-defined equipment specifications must resolve to implemented, permitted
catalog entries and satisfy source, technology and supernatural restrictions.
Equipment prerequisites refer to server-established owner definitions; owner
capacities and these definitions must come from trusted character compilation.
They are never accepted in a player command.

Weights are integer units chosen consistently by the campaign. Nested container
contents count against container limits; all owned items count once against
carrying capacity. Nonempty containers must be emptied before transfer or
consumption. Equipping requires an accessible individual item, its prerequisites
and a free slot. Readied equipment exposes catalog effects to the Wave 5 evaluator;
`carried_weight()` is the hook for an approved encumbrance rule. Consumption may
require an ammunition definition. Acquiring/spawning items is trusted scenario or
reward authoring, not a player command.

Game time is integer campaign ticks, independent of wall time. Advancing processes
due entries in `(due, id)` order. Supported scheduled operations remove an active
effect ID, restore a bounded resource pool, or emit an actor-targeted delayed
consequence event. Consequence events do not execute arbitrary code. Fired IDs,
command receipts and events survive JSON checkpoints. Reusing a command ID with
another payload or using a stale revision fails; an exact retry spends nothing.
Schedule and advance require a trusted engine capability outside the JSON payload.

## Transactions and integration

`ResourceService` connects the reducer to the existing SQLite/PostgreSQL campaign
transaction, immutable event log and snapshot/replay machinery. Trusted scenario
activation supplies the initial campaign and resource state through `create()`.
`execute()` checks the independently authenticated actor, exact campaign rules
and matching resource/campaign revisions. The transaction locks the campaign,
validates and applies the command, and writes the resource checkpoint and event
atomically. The log records the actual actor. Exact retries return the original
persisted result, including after later commands and process restart.

The resource checkpoint is excluded from the legacy public projection because
it includes all owners and scheduling data. The legacy demo turn path rejects
resource-managed campaigns to prevent two independent resource authorities.
Legacy demo saves remain readable. The prototype UI and its existing simplified
builder remain unchanged; wiring typed actions, authenticated transport and
perspective-filtered resource views belongs to #14/#19/#23. These modules provide
the engine/service entry points those waves consume.

Tests cover forged character fields, illegal purchases, repair validation,
attribute-effect dependencies, immutable revisions, item conservation, stale
commands, ownership, equipment gates, container constraints, schedule ordering,
expiration once, bounded recovery, reload and atomic database concurrency.
PostgreSQL runs under `WAYFARER_TEST_DATABASE_URL` in CI; SQLite runs locally.
