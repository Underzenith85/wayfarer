# Runtime configuration and operations

Configuration is validated before the server binds. Environment variables use
the `WAYFARER_` prefix: `HOST`, `PORT`, `DB`, `LOG_LEVEL`,
`OPENAI_API_KEY`, `OPENAI_MODEL`, `MODEL_TIMEOUT_SECONDS`, and
`DB_TIMEOUT_SECONDS`. CLI `--port` and `--db` override those two settings.
Provider key and model must be set together. Secrets use Pydantic SecretStr and
are never included in configuration output or logs.

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
and transitive packages before merging. Runtime dependencies are limited to the
async HTTP/database stack, validated settings and structured logging.
