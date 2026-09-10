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
