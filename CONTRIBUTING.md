# Contributing

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). CI uses
uv 0.11.33; use that version to reproduce its package builds. No global pip
installation of project packages or test tools is needed.

## Backend checks

The following matches the repository checks run before packaging. The full test
and release-evidence commands require the PostgreSQL service described in
[the test strategy](docs/testing.md) if they are to finish without integration
skips.

```bash
uv lock --check
uv sync --frozen
uv run --frozen wayfarer --help
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen python scripts/check_no_any.py
uv run --frozen python scripts/check_quality_gates.py
uv run --frozen python scripts/audit_gurps_sources.py
uv run --frozen python scripts/audit_gurps_equipment.py
uv run --frozen python scripts/validate_contracts.py
uv run --frozen python -m scripts.validate_live_contracts
uv run --frozen pytest --junitxml=artifacts/pytest.xml --cov-report=xml:artifacts/coverage.xml
uv run --frozen python -m scripts.release_gates artifacts/pytest.xml
uv build
```

For a focused test while developing, disable the repository-wide coverage floor:

```bash
uv run --frozen pytest --no-cov tests/test_relevant_domain.py
```

## Frontend checks

The frontend supports Node `^22.22.2`, `^24.15.0`, or `>=26.0.0`, and pins
pnpm 11.19.0.
Run its static and unit gates from the repository root with:

```bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend contracts:check
pnpm --dir frontend contracts:gate-test
pnpm --dir frontend fixtures:check
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend lint:design
pnpm --dir frontend format:check
pnpm --dir frontend test
pnpm --dir frontend build
```

Frozen-contract compatibility additionally needs `CONTRACT_BASE_SHA` set to the
full PR base commit before running `pnpm --dir frontend contracts:compat`.
Playwright browser suites and their backend requirements are documented in
[the release gates](docs/release-gates.md).

Add a runtime dependency with `uv add NAME`, or a development tool with
`uv add --dev NAME`. Review and commit both pyproject.toml and uv.lock. Frozen
sync does not regenerate the lockfile; CI separately checks lockfile freshness.
The runtime and development dependency sets are declared in `pyproject.toml` and
must remain reproducible from the committed lockfile.

The packaging workflow tests Python 3.14, builds the wheel from the source
distribution, installs it in a separate environment, and runs the HTTP/UI
smoke test from a temporary working directory. To reproduce that last step:

```bash
uv venv /tmp/wayfarer-wheel --python 3.14
uv pip install --python /tmp/wayfarer-wheel/bin/python dist/wayfarer-0.2.0-py3-none-any.whl
uv run --frozen python scripts/smoke_installed.py /tmp/wayfarer-wheel/bin/wayfarer
```

Keep pure domain imports independent of persistence, providers and HTTP. Add
behavioral tests for changes and follow [the architecture boundaries](docs/architecture.md).
Mypy strict and Ruff apply to source, tests and scripts. Install local checks with
`uv run --frozen pre-commit install`; run them with
`uv run --frozen pre-commit run --all-files`. Hooks run the locked Ruff, mypy,
explicit-`Any`, and pytest commands. See
[the quality contract](docs/quality.md) for typing policy, runtime validation,
repair commands and the pending required-check setting.
Pytest, pytest-asyncio, Hypothesis and branch coverage are documented in
[the test strategy](docs/testing.md).

Changes to the frozen player API must update contracts/v1 schemas, operation-bound
examples and documentation together. Run the offline contract validator and its
pytest regression cases; keep proposed endpoints separate until reviewed.
