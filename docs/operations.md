# Runtime configuration and operations

Configuration is validated before the server binds. Environment variables use
the `WAYFARER_` prefix. The current settings are:

- access and serving: `TOKENS`, `HOST`, `PORT`, `FRONTEND_DIR` and
  `ALLOWED_ORIGINS`;
- storage and ownership: `DB`, `DATABASE_URL`, `DB_TIMEOUT_SECONDS`, `PARTITION`,
  `CODEX_HOME` and `CODEX_SESSIONS`;
- providers: `LLM_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `CODEX_MODEL`,
  `CODEX_EFFORT` and `MODEL_TIMEOUT_SECONDS`;
- logging: `LOG_LEVEL`.

CLI `--port` and `--db` override those two settings. OpenAI API key and model must
be set together. The Codex provider uses its separate login and storage paths.
Secrets use Pydantic `SecretStr` and are never included in configuration output
or logs. `src/wayfarer/config.py` is the authoritative list and supplies defaults
and validation bounds.

`PARTITION` defaults to `default` and names the campaign/provider-job partition
owned by one runtime. Restart recovery only touches that partition. Horizontal
deployments must route a campaign consistently and must not start two workers
for the same partition; this is ownership, not a distributed lease queue.

With SQLite, the authoritative campaign database defaults to
`data/wayfarer.sqlite3` and the frozen player API uses the sibling
`data/wayfarer.v1.sqlite3` ledger. Back up both together. PostgreSQL replaces the
campaign store when `DATABASE_URL` is set, but the composed application still
uses the sibling local v1 ledger configured from `DB`.

The aiohttp server uses an application-scoped client session and closes it on
shutdown. Provider calls have a bounded timeout and propagate task cancellation.
SQLite uses aiosqlite, closes every connection and keeps deterministic mechanical
resolution inside the transaction without awaiting external services. SIGINT and
SIGTERM trigger aiohttp's graceful runner cleanup.

Structured JSON logs include request correlation IDs and stable error codes.
Known errors map to validation (400), missing state (404), revision conflicts
(409), storage failure (503), provider failure (502), and provider timeout (504).
Unexpected failures return a generic 500 while the traceback remains server-side.
Secret-like structured keys are redacted.

Update dependencies with `uv lock --upgrade` or one package with
`uv lock --upgrade-package NAME`; inspect changes, run all frozen gates, and
commit pyproject.toml with uv.lock. Review security advisories for changed direct
and transitive packages before merging. Runtime dependencies cover async HTTP and
SQLite/PostgreSQL access, validated settings, structured logging, the Codex
integration, and JSON Schema/reference validation.
