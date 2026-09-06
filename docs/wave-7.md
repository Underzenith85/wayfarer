# Wave 7: campaign approval and typed actions

## Campaign suitability (#9)

`PowerReviewer` wraps the character compiler with a separately versioned
`PowerPolicy`. Point-illegal proposals remain illegal regardless of GM approval.
Legal proposals may be blocked by forbidden combinations, require review because
of gross positive investment in selected definitions or capability benchmarks,
or qualify for bounded automatic approval. There is no universal balance score.
Negative-point purchases cannot hide investment in a concentration calculation.

`CharacterProposal` contains the existing strict draft and optional named custom
constructions. A construction groups distinct purchased catalog components with
server-bound effects; it cannot supply new costs, effects or discounts. Overlapping,
unpurchased or mechanically unbound components are blocked. Both explicit custom
constructions and catalog definitions with the `custom` hook require GM approval.
Parameterized mechanics not supported by the compiler remain unsupported.

Automatic approval is recorded explicitly. GM approvals require an authenticated
identity from the server's GM roster and a nonblank reason. Forbidden-combination
overrides require the explicit campaign override setting. Missing custom mechanics
and point-illegal builds cannot be overridden. Records bind campaign, actor,
build revision, review digest, rules revision, power-policy digest, findings,
approver, reason and campaign revision. Changes invalidate the record.

`CharacterCompiler.activate()` now requires a trusted authorization callback.
Application code activates through `PowerReviewer.activate()`, which revalidates
the proposal and its canonical approval before initializing runtime resources.
Approval objects must come from the service's ledger, never a player payload.
The callback is an internal server boundary, not a player capability.

## Typed action pipeline (#14)

`PlayService.propose()` validates a discriminated command with identity and
expected revision. Commands cannot supply rolls, success, target numbers, effects
or approvals. `preview()` evaluates feasibility without mutation or randomness.
Abandoned previews have no effect. `execute()` revalidates under the campaign
transaction lock and commits the result, state, resource changes and actual rolls
together. Questions and hypothetical commands, clarification requests, invalid
commands and unsupported actions create no event and spend no time or resources.

The declared original prototype mechanics are:

- Movement to an aware-of location through a directed world connection.
- Inspection and unopposed diplomacy checks configured by trusted scenario rules.
  Checks reference an implemented pinned skill and retain source/version, scenario
  modifier, effective target, actual dice, margin and outcome. Purchased and
  equipment effects feed the target; equipment attribute effects feed dependent
  skills with separate dependency traces. Success reveals only configured facts.
- Consumption of explicitly configured accessible, owned consumables. Quantity,
  availability and container constraints are checked; no healing is invented.
- Bounded waiting and configured action durations in game ticks, including due
  Wave 6 expiration/recovery processing. An optional explicitly configured fatigue
  cost is checked and spent once; the default is zero.
- Questions and hypothetical actions with no mechanical consequences.

Feasibility checks recorded character approval, authority, awareness, location,
conditions, incapacitation, readiness, equipment, quantities, fatigue and scenario
timing. Actor-owned prerequisite definitions and HP/FP limits are checked against
the server-compiled build. Both unknown and unknown-to-the-actor targets return
`target.unavailable`.

Attack proposals are typed and check target range and weapon readiness, but return
`combat.not_implemented`; combat implementation remains #16/#17. Deception and
intimidation return `adjudication_required`; a ruling workflow remains #15.
The current range model is same-location interaction, not tactical distance.

## Durable service contracts

`PlayService.create()` accepts trusted scenario/world/resource setup plus strict
character drafts. It recalculates resource prerequisites and HP/FP limits, records
automatic approvals, and leaves review-required actors inactive. GM approval uses
an explicit `ApproveCharacter` command through `PlayService.approve()`. Approvals
append to a durable ledger without advancing game time. Stale commands and
unauthorized identities cannot change the ledger.

A typed campaign has one `play_json` checkpoint containing world, actors, resources,
approvals and the committed result. Internal resource subcommands commit as one
campaign revision. SQLite and PostgreSQL use the existing transaction, event log,
snapshot and replay implementations. Retries return the original result after
later commands or restart, without rerolling or spending again. Scenario action
configuration, catalog pins, equipment bindings and power policy are fingerprinted;
changes require an explicit migration rather than a silent upgrade.

The legacy demo public projection excludes the checkpoint. Legacy demo turns and
standalone resource commands cannot mutate typed-play state. Full authenticated
transport, filtered dashboard views and LLM orchestration remain #19/#23/#20;
these service methods accept identities established by that trusted transport.
The existing demo builder is a compatibility path, not the new approval system.

Tests cover approval bypass attempts, custom mechanics, stale revisions, forbidden
combinations, no-op proposals, awareness/range/condition/equipment gates, exact
roll traces, conservation, competing commands, database rollback, replay across
snapshots and persistence of approval and scheduled expiration.
