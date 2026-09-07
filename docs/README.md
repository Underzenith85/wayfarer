# Wayfarer documentation

The root [README](../README.md) is the introduction and setup guide. Use this
directory for design, runtime, development, and feature documentation.

## Start here

- [Project overview](project-overview.md) — capabilities, trust boundaries, and
  current limitations
- [Architecture](architecture.md) — package boundaries and dependency direction
- [Frontend](frontend.md) — frontend toolchain, fixture mode, contracts, and tests
- [Runtime operations](operations.md) — configuration, storage, logging, and
  dependency maintenance
- [Contributing](../CONTRIBUTING.md) — local quality gates and contribution workflow

## APIs and persistence

- [v1 runtime](api-v1-runtime.md) — authenticated HTTP and live runtime behavior
- [Frozen v1 contract](../contracts/v1/README.md) — OpenAPI, schemas, examples,
  authorization, and compatibility policy
- [Persistence](persistence.md) — storage and transaction guarantees
- [Existing campaign migration](migration.md) — migration behavior and recovery

## Rules and quality

- [Rules catalog](rules-catalog.md) — implemented rules surface
- [Quality contract](quality.md) — typing, validation, CI, and repair commands
- [Testing](testing.md) — pytest, Hypothesis, integration, and browser strategy

## Implementation guides

- [Wave 6](wave-6.md) — character compiler, inventory, and game time
- [Wave 7](wave-7.md) — power approval and typed actions
- [Wave 8](wave-8.md) — adjudication, combat lifecycle, advancement, and scenes
- [Wave 9](wave-9.md) — outcomes, objectives, encounters, and subgroup time
- [Wave 10](wave-10.md) — Codex provider, NPC activity, capture, and recovery
- [Wave 11](wave-11.md) — campaign direction and scenario lifecycle
