# Project overview

## Capabilities

Wayfarer separates LLM interpretation and narration from authoritative game
mechanics. A provider may propose an intent or prose, but the engine validates
commands, rolls dice, applies consequences, and commits canonical state.

The current repository includes:

- Persistent campaigns, characters, inventory, location, time, discoveries,
  objectives, transcripts, rolls, and consequences
- Server-owned character costs and limits with validation before activation
- Scenario generation and editing over validated encounter structures
- Typed, allowlisted actions with deterministic mechanical resolution
- Idempotent commands, optimistic revisions, and transactional event history
- Combat, noncombat encounters, advancement, subgroup travel, capture, rescue,
  recovery, NPC plans, and campaign lifecycle services
- A packaged local demo and an independent responsive player frontend
- Optional Responses API and Codex subscription provider integrations

## Rules contract

The initial `wayfarer-lite-1` catalog is a deliberately limited,
GURPS-inspired prototype. It contains house rules and no proprietary rulebook
text. The current baseline includes bounded attributes, a closed trait catalog,
trained skills, 3d6 roll-under checks, and validated scenario mechanics.

Point legality alone does not establish balance for a complete ruleset. New
traits and powers require implemented effects, prerequisites, incompatibilities,
challenge budgets, and an approved rules source. The supported mechanics are
documented in the [rules catalog](rules-catalog.md) and wave guides.

## Trust boundary

Canonical state comes from committed engine outcomes, never generated narration.
The engine does not allow a model to choose target numbers, modifiers, dice
results, rewards, or arbitrary state mutations. Hidden facts are filtered from
player context until the engine marks them discovered.

Campaigns pin a rules version and revision. Commands use per-campaign request
identities, so an exact retry returns its prior result instead of executing
twice. Reusing an identity with different input is rejected. Provider failure
before interpretation changes nothing; narration failure after commit preserves
and exposes the mechanical result.

## Repository layout

- `src/wayfarer/` — domain, rules, simulation, orchestration, persistence,
  providers, and transports
- `frontend/` — independent React player interface
- `contracts/v1/` — frozen HTTP and event contracts
- `docs/` — architecture, operations, rules, testing, and implementation guides
- `tests/` — backend unit, property, integration, and end-to-end coverage

The packaged `wayfarer` command serves the legacy local demo, including its
static assets. The authenticated campaign application and independent frontend
are separate integration surfaces.

## Current limitations

Wayfarer remains a local-first prototype, not a hardened public deployment.
Authentication and authorization exist on the typed campaign surface, but the
legacy demo is single-user and loopback-only. Do not expose the development
server publicly.

The rules catalog is intentionally incomplete, narrative prose is not formally
verified against canonical facts, and some frontend journeys remain explicit
fixtures while live transport integration proceeds. Long campaigns will need
transcript pagination and context budgeting. Voice input/output is currently
browser-assisted or tracked as later transport work rather than a complete
realtime voice stack.

For implementation status and precise contracts, use the
[documentation index](README.md).
