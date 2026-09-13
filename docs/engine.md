# Engine guide

The Wayfarer engine is the authoritative, deterministic part of play. It owns
character legality, rule configuration, world and campaign state, and the state
transitions that resolve commands. It does not own HTTP, storage, clocks, model
calls, or generated narration.

This page is the starting point for contributors working in
`src/wayfarer/engine/`. It describes the engine's shape and the path a command
takes. The [architecture decisions](architecture.md) remain authoritative for
dependency rules and persistence design; the rule-specific documents describe
individual mechanics.

## Mental model

At its boundary, the engine behaves like a deterministic reducer:

```text
configured rules + checkpoint + typed command + explicit random source
                              |
                              v
                     validate and resolve
                              |
                              v
                    new checkpoint + events
```

The caller supplies every input that could affect the result. In particular,
randomness arrives through `RandomSource`; the engine does not read system
entropy or a wall clock. Given the same configuration, state, command, and seed,
resolution must produce the same state and event facts.

Most records are immutable. Domain verbs return updated records with
`model_copy`, `dataclasses.replace`, or a newly constructed value. The service
layer is responsible for locking a campaign, checking its expected revision,
and atomically committing the returned state and events.

## Package map

| Package             | Responsibility            | Typical contents                                                                                                              |
| ------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `engine.rules`      | Stateless rules knowledge | Rule definitions, catalogs, tables, profiles, checks, and shared typed vocabularies                                           |
| `engine.character`  | Character construction    | Draft compilation, point accounting, derived statistics, trait projections, and approval                                      |
| `engine.simulation` | Stateful play             | Commands, checkpoints, reducers, events, combat, health, movement, magic, equipment, social activity, and campaign procedures |
| `engine.world`      | Shared world model        | Entities, connections, facts, knowledge, beliefs, commitments, and actor perspectives                                         |

The repository kernel sits immediately above the engine in `wayfarer.models`,
`wayfarer.validation`, and `wayfarer.errors`. Engine modules may use that kernel.
They must not import application payloads from `wayfarer.contracts`.

Package `__init__.py` files do not provide convenience re-exports. Import the
concrete module that owns a type or operation. This keeps dependency costs and
cycles visible.

## The main play contracts

The general action path is centered on these types:

- `ActionRules` in `engine/simulation/actions.py` is the configured rules graph
  for one campaign.
- `PlayState` in the same module is the canonical play checkpoint. It contains
  the world, resources, actors, encounters, and domain-specific campaign state.
- `TypedAction` is the discriminated union for the general move, inspect,
  attack, item, social, wait, and question commands.
- `ActionEngine` in `engine/simulation/action_engine/engine.py` validates a
  checkpoint and resolves a `TypedAction`.
- `EngineEvent` in `engine/simulation/events.py` is the discriminated union of
  state patches and typed facts emitted by resolution.
- `RulesContext` in `engine/simulation/rules_context.py` carries explicit domain
  dependencies into state-aware mechanic reducers.

Specialized domains also define their own typed commands and reducers. Combat,
resources, health, spells, abilities, vehicles, and campaign procedures should
reuse their existing domain engine instead of introducing an alternate path for
the same mechanic.

## What happens when a command resolves

For a general action, the application layer binds a campaign's pinned character,
resource, combat, and action rules into an `ActionEngine`. It then passes the
current `PlayState`, a typed command, and an explicit random source to
`ActionEngine.resolve`.

Resolution follows this contract:

1. Validate the checkpoint against the engine's configuration digest and every
   enabled aggregate invariant.
2. Assess whether the command is feasible, rejected, unsupported, or requires
   clarification or adjudication.
3. Apply the selected domain transition, consuming random draws in a stable
   order where necessary.
4. Advance the checkpoint revision for a committed action and validate the
   resulting state.
5. Return the updated `PlayState` and ordered `EngineEvent` values.

An assessment that does not commit can still return an `ActionResolved` fact,
but it must not smuggle a state change into the checkpoint. Persistence decides
whether and how the returned command result is committed; the engine performs no
I/O itself.

## State and events

`PlayCheckpoint` holds canonical state. `PlayState` adds a compatibility event
projection for older API and replay boundaries, but that projection is not part
of the canonical checkpoint.

Engine events have two distinct jobs:

- `StatePatched` values describe explicit path operations used to rebuild
  canonical state.
- Typed facts such as `ActionResolved`, `ResourceChanged`, and
  `CombatResolved` describe what happened and declare their audience.

Events are ordered. Replaying their state patches from the same genesis state
must reconstruct the same checkpoint digest. Narration is a separate,
non-authoritative stream and never participates in the fold. See
[persistence](persistence.md) for the durable command, event, and snapshot model.

## Randomness and replay

Mechanics depend on the `RandomSource` protocol in
`engine/rules/checks.py`. Use its shared helpers:

- `draw_dice` for six-sided dice, with checks defaulting to three dice;
- `draw_index` for non-die selection from a finite set;
- `evaluate_success` when recorded dice are already available.

Production uses the versioned `SeededRandom` implementation in
`engine/rules/randomness.py`. Calling a mechanic that needs randomness without
supplying a source fails closed through `NO_RANDOM`. New code must preserve draw
order because recorded seeds and traces are replay evidence.

## Rules, profiles, and certification

A rule definition or catalog row is not proof that the engine can execute the
mechanic. Runtime availability is selected through pinned packages, rules
profiles, and the conformance registry. Unsupported combinations are rejected
rather than delegated to narration.

Certification lives in `wayfarer.certification`, outside the engine. Its source
ledgers account for implementation and review evidence; the engine must not read
source PDFs, audit ledgers, or release state. See [rules catalogs](rules-catalog.md),
[rules profiles](rules-profiles.md), [GURPS conformance](gurps-conformance.md), and
[Basic Set certification](gurps-basic-set-certification.md) for those separate
contracts.

## Where new code belongs

Use the lowest layer that owns the behavior:

- Put a formula or lookup with no campaign state in `engine.rules`.
- Put character purchasing, compilation, or derived build state in
  `engine.character`.
- Put a transition over `PlayState` or another runtime aggregate in the matching
  `engine.simulation` domain.
- Put shared world entities and perspective rules in `engine.world`.
- Put transactions, retries, providers, and user-facing workflow outside the
  engine.

Keep nouns and verbs separate. Aggregate modules own records and invariants;
engine or transition modules own operations over them. If behavior branches by
command or rule kind, prefer a typed dispatch map with one focused handler per
kind. Do not use function-local imports to conceal a dependency cycle.

## Validation and tests

Engine changes should normally include focused unit tests for the affected rule
or reducer, negative tests for invalid commands or state, and deterministic
evidence for any random path. Changes to persisted behavior may also need replay
fixtures and schema compatibility coverage.

Run a focused test while iterating without applying the repository-wide coverage
threshold:

```bash
uv run --frozen pytest --no-cov tests/test_relevant_domain.py
```

Before handing off a completed engine change, run the full checks appropriate to
its scope. The baseline commands are:

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest
```

`tests/test_architecture.py` enforces dependency direction, import behavior,
entity ownership, reducer seams, and the branch-complexity budget. Read
[testing](testing.md), [quality](quality.md), and
[release gates](release-gates.md) before changing replay, schemas, or certified
rules behavior.

## Next documentation slices

This guide establishes the engine-wide vocabulary. Useful follow-up pages are:

1. Constructing a rules profile and compiling a character.
2. Building an initial `PlayState` from a scenario.
3. Writing a domain command and reducer, including validation and event output.
4. Following one action from proposal through commit and replay.
5. Maintaining event and checkpoint compatibility across schema changes.
