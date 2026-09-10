# ADR 001: Python package and domain boundaries

Status: accepted for wave 1 / issue #2.

## Decision

Keep Python and deliver an installable modular monolith. The prototype does not
justify separate services or a language rewrite. Use `src/wayfarer`, absolute
package imports, PEP 621 metadata and a console entry point. No `sys.path` edits,
implicit checkout imports, or global pip dependencies are required.

| Package | Owns | Permitted internal dependencies |
| --- | --- | --- |
| `models`, `validation` | The `Record` entity contract, shared typed contracts and runtime structural schemas | Models |
| `rules` | Closed demo catalog and checks | Models, validation, rules |
| `character` | Character draft validation, preset, and profile-selected attribute/secondary statistics | Models, validation, rules, character |
| `simulation` | Scenario validation and state transitions | Models, validation, rules, character, simulation |
| `persistence` | SQLite schema, transactions and storage | Models, validation |
| `orchestration` | LLM adapter, intent, application use cases | Domain packages, persistence |
| `transport` | HTTP routes and bundled static UI | Orchestration and read-only domain APIs |
| `cli` | Configuration and server composition | Orchestration, transport |

The simulation resolver mutates the supplied state and returns an event. The
SQLite adapter invokes it only after locking and checking the expected revision,
then atomically saves both state and event. The callback must not perform external
I/O. LLM calls occur before or after this transaction, never inside it. Typed contracts establish the seams. Mypy strict now checks the whole Python
repository, including tests and scripts; shared runtime schemas validate
untrusted data before domain use.

Domain boundary tests inspect imports and verify that importing the resolver does
not load HTTP, storage or provider adapters. New dependencies must follow the
same direction. Persistence contains all SQL; orchestration coordinates it rather
than issuing SQL itself.

## Entities and verbs

Nouns and verbs are kept apart at three levels.

- **One entity contract.** Every domain record subclasses `wayfarer.models.Record`:
  frozen, strict, closed to unknown fields and revalidated when nested. No other
  module subclasses `BaseModel` directly or restates that configuration. A state
  transition returns a new record; entities never mutate themselves.
- **Aggregate modules hold entities and their invariants.** `simulation/scenes.py`,
  `party.py`, `noncombat.py`, `access.py`, `advancement.py`, `spell_bindings.py`,
  `ability_types.py` and `combat.py` each declare their records and a
  `validate_*` function that checks a checkpoint against those records. Pure
  rule tables that need no state (range penalties, rapid-fire bonuses) live in
  `rules/`.
- **Verbs live in engines and services.** `simulation/actions.py` declares the
  typed commands, action rules, results and `PlayState`; the resolver that
  assesses and applies them is `simulation/action_engine.py`, whose `validate`
  is the ordered sequence of aggregate invariants. `CombatEngine` and
  `ResourceEngine` follow the same shape. Orchestration services coordinate
  transactions and end every one with `PlayService.commit`, the only verb that
  writes a play checkpoint onto a campaign row (setup transitions, which write
  several campaign fields at once, are the documented exception).

Architecture tests enforce the single entity base, that importing the action
entities never loads the engine, and that no other module assigns `play_json`.

### Transaction reducer seams (#417)

Combat, physical procedures, spells, abilities, spell backfires, setup and the
director use named steps. Their transaction callbacks load the checkpoint, call
the reducer, run the existing checkpoint hooks where applicable, and commit.
The architecture gate limits functions in these seven modules to 200 lines and
requires their transaction callbacks to remain straight sequences without nested
closures.

Command-scoped context records supply the dependencies previously captured by
large callbacks. Combat dispatches by command kind after normalizing interrupted
turns; its steps keep pre-turn state and the resource accumulator explicit until
settlement. Physical procedures, setup operations, backfire effects and director
phases likewise use dispatch tables. Director provider calls and durable phase
saves remain outside domain transactions.

The `reduce_*` entry points return new state and a result without committing.
Setup returns a separate campaign value because activation changes several
campaign fields, including the nested scenario. Existing service entry points,
receipts, checkpoint ordering, dice consumption and transport contracts stay the
same. Contexts still use the existing `PlayService` rule adapters; replacing
those handles with narrower rules dependencies belongs to #415. Seed recording
and typed event streams remain the separate ADR 002 migration steps.

### Direction set by the open issues

The open engine issues fix the next moves in the same direction, so refactors
should land on these seams rather than invent new ones.

- **Invariants name their node (#363, #365).** `ValidationError` carries a
  `reference` to the offending check, alternative or rule family, and the
  scenario studio reports that locus instead of the scenario id. New
  `validate_*` functions must raise with a reference whenever one exists.
- **One owner per spatial fact (#323, #324, #326, #329).** Combat state is
  moving to a discriminated basic, square and hex spatial context. Until it
  lands, keep square and hex vocabularies distinct (`Facing` versus
  `HexFacing`), keep `RangedSituation` the only distance store, and do not add
  battlefield or placement requirements to shared schemas.
- **One engine, one transaction (#181, #290, #397).** Vehicle, object and
  ranged follow-ups reuse `simulation.transport`, `simulation.combat` and the
  injury and object reducers, and every commit still passes through
  `PlayService.commit` under the existing campaign transaction. A second
  resolver for the same rules is a defect.
- **Rule math belongs below orchestration (#94 catalog lane, #173, #176).**
  Tables and formulas with no state dependency go in `rules/`; transitions on
  `PlayState` go in `simulation/`. `orchestration/gurps_melee.py`,
  `gurps_ranged.py` and `unarmed.py` still carry Basic Set tables and raw dice;
  move each with the issue that owns that mechanic, never as a drive-by.

## Package and dependency workflow

`pyproject.toml` is authoritative; `uv.lock` is committed. Runtime dependencies remain empty. The development group contains locked mypy,
Ruff and pre-commit tooling; the existing unittest suite uses the standard library. The exact uv build backend version is pinned
for repeatable builds and bundled by the documented uv CLI version.

CPython 3.14 is the development and CI interpreter. `requires-python >=3.14`
sets the same minimum for installed packages; newer interpreters remain allowed
without claiming CI coverage.

The wheel includes the web assets under `wayfarer.transport.static`, accessed
with `importlib.resources`. It has no dependency on a repository-relative static
folder. Database paths remain caller-owned, never inside the installed package.

## Compatibility and scope

The HTTP routes, JSON payloads, SQLite schema, rules version and demo game behavior
are preserved. `uv run server.py` remains a compatibility launcher after syncing.
The old root-level Python modules are internal implementation details and are
replaced with explicit package imports. No full GURPS implementation is implied.

Strict typing/Ruff gates are implemented in wave 2 (#3); see docs/quality.md.
Pytest/Hypothesis belongs to #4, and production
configuration, async I/O and error handling are implemented in wave 3 (#5). Existing demo limitations
remain documented, including the synchronous local HTTP server and narration
fallback. This wave introduces no authentication or production deployment.

# ADR 002: Layers, streams and where non-determinism enters

Status: proposed. Records the target shape agreed while refactoring entities
and verbs; each step below is additive and lands behind the existing gates.

## Layers

| Layer | Owns | Today |
| --- | --- | --- |
| Clients | Web, mobile and voice front ends against one contract | `frontend/`, generated clients |
| API | Frozen v1 HTTP and live-event contracts, authentication, projection | `transport/` |
| Orchestrator | One session per active campaign, per-campaign serialization, entropy, clocks, LLM jobs, the outbox | `orchestration/` |
| Engine | Pure GURPS rules and state transitions | `rules/`, `character/`, `simulation/` |
| Persistence | Command log, event stream, narration stream, snapshots | `persistence/` |

The engine signature is `resolve(state, command, rng) -> (state, events)` and
the engine never imports entropy, clocks, storage, providers or HTTP. The
orchestrator is the only layer that knows more than one campaign exists.

## Three streams, one authority

- **Command log (authoritative).** The typed command, the acting principal,
  `expected_revision`, the entropy seed drawn for it, and for model-interpreted
  turns the original text and proposal id. Rulings and GM declarations of
  missing spatial facts are commands like any other.
- **Event stream (derived, replayable).** What the engine reports happened:
  the typed `ResourceEvent`, `SceneEvent`, `SpellEvent` and their siblings,
  emitted as a list rather than buried in state.
- **Narration stream (non-authoritative).** Generated prose keyed by campaign
  and revision. Rebuilding state ignores it; players keep the text they read.

Snapshots of `PlayState` per revision remain, as a cache:
`state(n) = fold(snapshot(k), events[k+1:n])`. Character state is a projection
of the campaign stream, not a second aggregate; the workshop draft is the one
separate aggregate, because it exists before a character enters a campaign.

## Where randomness, judgement and narration enter

1. **Interpretation** runs before the transaction. Free text goes to the model,
   which returns a typed proposal; the command log records the resulting
   command. Replay never calls the model. NPC and director decisions follow the
   same path from proposal to validated command.
2. **Dice** are orchestrator-owned. One seed is drawn per command and stored in
   the command record; the engine receives a deterministic `RandomSource` built
   from it. `CheckTrace.dice` already records every draw as output, so replay
   asserts the recorded traces reappear. End-of-turn hooks run in the same
   transaction on the same seeded source.
3. **Judgement** is a command. `DecideRuling` already is; declarations of
   missing spatial facts (#324) and wall-clock inputs such as invitation expiry
   must be too. Nothing inside the fold reads the clock.
4. **Narration** runs after commit from the committed events and projection and
   is written to its own stream. A failed narration logs and leaves the turn
   committed.

## Scenarios

A scenario is configuration plus genesis state. It is never executed and it is
never mutated by play.

- **Authoring is its own aggregate.** A `ScenarioDocument` is edited, imported
  or generated in the catalog; each change is a receipt in `scenario_receipts`,
  and model generation is a proposal into this aggregate, not into a campaign.
  `ScenarioStudio.validate` checks playability by instantiating the real
  `ActionEngine` and calling `initial_state`, so authoring is judged by the
  engine that will play it, never by a second rules implementation. A
  `PublishedRevision` is immutable and carries its content digest, the engine
  digest it was validated against and the party digest.
- **Activation is the genesis command.** Setup's seat, readiness and activate
  operations are commands in the pre-play segment of the campaign stream, and
  `activate` is the one that produces revision 0 by folding the published
  graph's world, initial resources and pregenerated actors into `PlayState`.
  The genesis command carries the authoritative scenario reference: catalog
  id, revision and content digest, checked against the published revision on
  load. The graph copied onto the campaign row is a cache of that revision.
- **The graph becomes rules and world, not mechanics.**
  `ScenarioContent.runtime_rules()` folds scenes, objectives, checks, NPC
  plans, recovery, party, spells and combat equipment into `ActionRules`, which
  `PlayService.bind` compiles into the engine for that campaign. The result is
  part of `configuration_digest`, so replay pins a scenario revision exactly as
  it pins a rulebook. Mechanics come only from the rules profile in
  `rules_ref`; `single_mechanics_source` rejects a document that supplies its
  own. During play the scenario is inert: learned facts, scene cursors,
  objective progress and NPC clocks live in `PlayState`, and scenario prose
  reaches only the narrator's context.
- **Continuation is a migration command.** `continuation.prepare` merges a next
  graph with the old world under fixed rules: existing records win, builds are
  preserved, settled rewards cannot recur, captives cannot be relocated. It
  changes the digest, which is a replay boundary.

### Maps

There are three maps, and the scenario must own the templates for all of them
in one place.

- **World graph.** Locations and connections are authored in the scenario's
  `World`, and every `Scene` binds to a `location_id`. Where each actor stands
  is state (`Entity.location_id`, `ActorScene`), rewritten by move and travel
  commands.
- **Battlefield templates.** Square `Battlefield` templates are authored in
  `CombatRules.battlefields`, each tied to a location, and are inside the
  pinned digest. `StartEncounter` names a template and supplies placements;
  the encounter holds the template id and each combatant's position and
  facing. `validate_contexts` requires participants to share one scene whose
  location matches the template's location, which is where the two maps meet.
- **Hex maps are the exception to fix.** `HexBattlefield` is embedded whole in
  `Encounter.hex_battlefield`, so a square map is pinned configuration while a
  hex map is per-encounter state in the snapshot. That is the duplicate owner
  #323 names. The target is one tagged union of square and hex templates under
  `CombatRules.battlefields`, an encounter that holds a template reference and
  a spatial-context instance, and an explicit `MigrationEntry` for legacy
  snapshots that embed a map. Mapless combat (#324) is a scene with no
  template, whose spatial facts arrive as `RangedSituation` and GM declaration
  commands. Which map a client draws is a projection, never a mechanic.

## Consequences

Replay by re-execution makes the engine's dice consumption order part of its
contract. A refactor that reorders two checks changes the traces for the same
seed, so such a change is a rules migration event, exactly like a rulebook
change. If that proves too strict, record the dice tape instead of the seed.

Only the command log is authoritative. `PlayService.commit` is the single
writer of play state today, and the event store gets the same single-writer
test.

Replay is valid only under the pinned rules digest. `configuration_digest`
and `MigrationEntry` already exist; the gate is that a stream's digest must
match the engine that replays it.

## Migration order

1. Record the seed per command and pass a seeded `RandomSource` into the engine.
2. Store the interpretation proposal beside the command.
3. Emit engine events as a list and append them to their own stream.
4. Add the replay gate: rebuild every fixture campaign from its command log and
   compare to its snapshot.
5. Move snapshots to cache status.
6. Carry the scenario reference (catalog id, revision, content digest) on the
   genesis and continuation commands and verify it on load; treat setup's
   writes as pre-play commands of the same stream.
7. Give map templates one owner: square and hex templates under
   `CombatRules.battlefields`, a template reference plus spatial-context
   instance on the encounter, and a migration for snapshots that embed a hex
   map (with #323).
