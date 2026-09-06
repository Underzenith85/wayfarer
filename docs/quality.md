# Python quality contract

The authoritative checker is **mypy strict**. Ruff owns linting, import ordering
and formatting. Tool versions and transitive development dependencies are pinned
by `uv.lock`; the runtime still has no third-party dependencies.

## Scope and rules

Mypy covers all source, tests, scripts and the compatibility launcher. Public
and domain functions require full annotations. Use TypedDicts for serialized
contracts, Literal-tagged unions for variant results, and Protocols for injectable
behavior. The package ships `py.typed` for downstream users.

Explicit `Any` is rejected by `scripts/check_no_any.py`; mypy rejects unfollowed-import Any. The separate source gate avoids a false positive from Pydantic Settings’ generated constructor while retaining the project rule. Do not hide errors with
casts, blanket ignores, missing-import ignores, excluded modules or per-file
checker overrides. An unavoidable targeted suppression requires its error code,
an explanation and review; unused suppressions fail. None are currently needed.

Treat decoded JSON as `object`. `validation.py` checks primitive types and object
shapes before returning domain contracts. In particular, booleans are not integer
allocations, strings are not silently converted to numbers, unknown character
fields are rejected, and loaded campaign records are checked before simulation.
These small runtime schemas serve the current closed demo; character legality
remains the builder's responsibility. They are not the future complete ruleset.

The LLM's selected action passes a Literal allowlist before execution. The
persistence result is a discriminated committed/replayed union, so a replay cannot
accidentally provide a new mechanical event. A RandomSource protocol permits
typed deterministic fakes while production checks retain server-owned randomness.

## Commands shared by hooks and CI

```bash
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
```

For automated repairs, use `uv run --frozen ruff check --fix .` and
`uv run --frozen ruff format .`, then review the diff. Hooks check rather than
rewrite files and run against the whole repository using the locked uv environment:

```bash
uv run --frozen pre-commit install
uv run --frozen pre-commit run --all-files
```

CI also verifies lock freshness, runs tests, builds and installs the wheel, and
exercises its HTTP interface from outside the checkout. The negative gate probe
creates temporary malformed fixtures and proves each checker fails for the
expected defect:

```bash
uv run --frozen python scripts/check_quality_gates.py
```

Each matrix job fails immediately when a real gate fails. The probe is a test of
the gates, not a substitute for checking the actual repository.

## Required checks: configuration pending

The workflow runs on pushes and PRs for Python 3.12, 3.13 and 3.14. An administrator
must make `package (3.12)`, `package (3.13)` and `package (3.14)` required on `main`
after confirming the emitted job names. Preserve existing protection settings.

This repository setting is tracked in [issue #27](https://github.com/Underzenith85/wayfarer/issues/27).
It is **not configured by this PR**: the connected tools cannot administer branch
protection/rulesets. Do not claim merge protection until the setting is verified.
