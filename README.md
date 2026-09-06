# Wayfarer

A local-first, service-backed roleplaying prototype with an LLM game master, constrained character generation, a scenario studio, and voice/text play.

## Run

Python 3.12+ with [uv](https://docs.astral.sh/uv/). CI targets CPython 3.12–3.14. No third-party runtime dependencies.

```bash
uv sync --frozen
```

```bash
uv run --frozen wayfarer
```

Open http://127.0.0.1:8000. Click **Begin the demo adventure**, or use the Character workshop and Scenario studio to create a campaign. SQLite saves state under `data/`; browser storage holds only the selected campaign ID. Back up the SQLite database to preserve campaigns.

The default **Offline demo** uses preset generation and a small keyword action classifier. It is explicitly not an LLM simulation. For real generation, intent classification, and narration, export credentials on the server:

```bash
export OPENAI_API_KEY='your-key'
export OPENAI_MODEL='your-structured-output-capable-model'
uv run --frozen wayfarer
```

Choose a model your API account can access that supports Responses API structured outputs. No API credentials are exposed to the client or saved in Git. API calls incur usage charges. Optional integration uses [Responses structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs). Provider integration is covered with mocks; live provider access requires your credentials.

## Prototype features

- Three responsive workspaces: Adventure, Character workshop, Scenario studio.
- Characters, inventory, location, elapsed time, discoveries, narrative consequences, transcript and rolls saved by a SQLite service.
- LLM character proposals validated against server-owned costs and limits; invalid drafts cannot activate. Manual editing and validation supported.
- LLM scenario generation skins a fixed mystery encounter graph. Draft editing and activation into separate campaigns.
- LLM intent classification into typed, allowlisted actions; server dice and consequences; separate flavor narration after state commit.
- Idempotent turn requests and optimistic revisions; SQLite transactions atomically save state and event history.
- Browser speech recognition fills an editable text draft; it never submits automatically. Optional speech synthesis reads narration aloud. Recognition availability depends on browser, permissions and secure context (localhost is supported by many browsers). Speech may use the browser vendor’s remote service. Text always works.

## Rules contract: `wayfarer-lite-1`

This is a **GURPS-inspired, deliberately limited prototype**, not a complete or officially licensed GURPS implementation. No proprietary rulebook text is included. The trait catalog and encounter mechanics below are prototype house rules, not representations of exact published traits.

- 100-point budget; ST/HT cost 10 points per level relative to 10; DX/IQ cost 20. Attributes restricted to 8–14.
- Reduced attributes plus negative traits cannot contribute more than 25 points.
- Closed trait catalog: Keen senses +5, Fit +5, Curious −5, Code of honor −10. Traits currently have no automated situational effects; their costs are prototype-defined.
- Four trained skills; allocations 1/2/4/8/12/16; attribute-relative progression with skill ceiling 16. Unknown skills, duplicate traits, unknown fields, noninteger values and negative allocations rejected. No default/untrained skill checks.
- 3d6 roll-under including critical success/failure boundaries. Engine selects skills; LLM cannot select target values, modifiers, roll results, rewards or arbitrary mutations.
- Investigation/conversation reveal one lead; stealth after finding it completes the mystery. Failed checks cost one FP and ten minutes. Rest restores one FP in thirty minutes. These are prototype scenario mechanics.
- No combat, spells, custom powers, equipment shopping, leveling, or arbitrary action execution yet. Flavor/backstory grants no mechanical benefits.

Point legality does not guarantee balance for a full ruleset. Expanding the catalog requires mechanically implemented traits, prerequisites, incompatibility rules, scenario challenge budgets and an approved rules source.

## Architecture

The installed `wayfarer` command starts the packaged demo. Code lives under `src/wayfarer`, with rules, character, simulation, persistence, orchestration and transport boundaries. Vanilla HTML/CSS/JS ships inside the wheel. See [architecture](docs/architecture.md), [contributing](CONTRIBUTING.md), and [existing campaign migration](docs/migration.md).

`uv run server.py` remains a compatibility launcher. From another directory, use an installed `wayfarer --db /absolute/path/to/campaigns.sqlite3`; relative database paths are relative to the launch directory.

The LLM proposes, the engine validates/resolves, and committed facts drive narration. Narration is presentation only and cannot become canonical state. The UI exposes committed outcomes alongside generated prose. Initial hidden clues/secrets are removed from play responses and intent context until discovered; the scenario studio is deliberately an author view with spoilers.

Campaigns have a revision and pinned rules version. Turn events have a per-campaign unique request ID. Retrying an ID returns its original committed result without executing again; reusing it with different input is rejected. PostgreSQL is selected with `WAYFARER_DATABASE_URL`; SQLite remains the local default. See [rules catalog](docs/rules-catalog.md) and [persistence](docs/persistence.md). A failed intent call applies nothing; a failed narration call leaves the mechanical outcome saved and visible.

## Test

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest
uv lock --check
uv build
node --check src/wayfarer/transport/static/app.js
```

Pytest/Hypothesis tests cover character abuse, critical roll edges, secret filtering, stale state, retries, persistent state, progression/reward duplication, invalid activation and provider failures.

## Boundaries and next steps

Single-user localhost prototype: no authentication, multiplayer or hosted deployment. The aiohttp service provides async I/O and graceful shutdown but still binds locally. Do not expose the development server publicly. The server binds loopback and rejects cross-origin JSON writes. LLM calls are bounded by a 45-second timeout. Long-running campaigns need transcript pagination/context budgets; scenario generation needs a richer validated encounter graph. Narrative prose is not formally verified and can diverge from canonical facts; inspect committed outcomes when needed.

Next: production API/auth, full versioned rules catalogs, general typed action planner, per-character knowledge, NPC mechanics, combat and advancement, realtime voice, model evaluations, browser accessibility and interaction QA.

Python source, tests and scripts pass mypy strict and Ruff. See the [quality contract](docs/quality.md) for hooks, CI gates and required-check setup.

Runtime configuration, error mapping, logging and dependency updates are documented in [operations](docs/operations.md); the pytest/Hypothesis strategy is in [testing](docs/testing.md).
