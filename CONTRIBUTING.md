# Contributing

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). CI uses
uv 0.11.33; use that version to reproduce its package builds. No global pip
installation of project packages or test tools is needed.

```bash
uv sync --frozen
uv run --frozen wayfarer --help
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen python scripts/check_quality_gates.py
uv run --frozen python scripts/validate_contracts.py
uv run --frozen pytest
uv lock --check
uv build
```

Add a runtime dependency with `uv add NAME`, or a development tool with
`uv add --dev NAME`. Review and commit both pyproject.toml and uv.lock. Frozen
sync does not regenerate the lockfile; CI separately checks lockfile freshness.
The current runtime and test suite intentionally have no third-party dependencies.

The packaging workflow tests supported Python versions, builds the wheel from the
source distribution, installs it in a separate environment, and runs the HTTP/UI
smoke test from a temporary working directory. To reproduce that last step:

```bash
uv venv /tmp/wayfarer-wheel --python 3.12
uv pip install --python /tmp/wayfarer-wheel/bin/python dist/wayfarer-0.2.0-py3-none-any.whl
uv run --frozen python scripts/smoke_installed.py /tmp/wayfarer-wheel/bin/wayfarer
```

Keep pure domain imports independent of persistence, providers and HTTP. Add
behavioral tests for changes and follow [the architecture boundaries](docs/architecture.md).
Mypy strict and Ruff apply to source, tests and scripts. Install local checks with
`uv run --frozen pre-commit install`; run them with
`uv run --frozen pre-commit run --all-files`. Hooks use the exact CI commands and
locked tools. See [the quality contract](docs/quality.md) for typing policy,
runtime validation, repair commands and the pending required-check setting.
Pytest, pytest-asyncio, Hypothesis and branch coverage are documented in docs/testing.md.


Changes to the frozen player API must update contracts/v1 schemas, operation-bound
examples and documentation together. Run the offline contract validator and its
pytest regression cases; keep proposed endpoints separate until reviewed.
