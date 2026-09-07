# Wayfarer

Wayfarer is a local-first, service-backed roleplaying engine and player interface.
It combines persistent campaign state, constrained character and scenario creation,
typed mechanical resolution, and optional LLM-assisted game-master behavior. The
rules are GURPS-inspired prototype house rules; Wayfarer is not a complete or
officially licensed GURPS implementation.

The backend includes a packaged local demo. The independent React frontend is under
`frontend/` and is still being integrated with the authenticated campaign API.
Detailed guides and reference material are indexed in [`docs/`](docs/README.md).

## Setup

### Requirements

- CPython 3.14
- [uv 0.11.33](https://docs.astral.sh/uv/getting-started/installation/)
- Optional frontend: a supported Node.js release and pnpm 11.19.0 (see
  [frontend setup](docs/frontend.md))

### Run the local backend

Install the locked dependencies and start the packaged demo:

```bash
uv sync --frozen
uv run --frozen wayfarer
```

Open <http://127.0.0.1:8000>. The offline demo requires no model credentials.
Campaign state is stored in `data/wayfarer.sqlite3` by default; back up that file
to preserve local campaigns.

The server accepts `--port` and `--db` overrides:

```bash
uv run --frozen wayfarer --port 8080 --db /absolute/path/to/campaigns.sqlite3
```

### Enable the OpenAI Responses provider

The application does not automatically load `.env` files. Export both required
settings in the shell that starts Wayfarer:

```bash
export WAYFARER_OPENAI_API_KEY='your-api-key'
export WAYFARER_OPENAI_MODEL='a-structured-output-capable-model'
uv run --frozen wayfarer
```

Use a model available to your API account that supports Responses API structured
outputs. Credentials remain server-side, and API usage is billed separately.

For the dedicated Codex subscription login and typed campaign provider, follow the
[Codex provider setup](docs/wave-10.md#codex-subscription-setup).

### Run the frontend

From `frontend/`:

```bash
corepack enable
corepack prepare pnpm@11.19.0 --activate
pnpm install --frozen-lockfile
pnpm dev
```

Open <http://127.0.0.1:5173>. The frontend does not require a backend to build, but
normal mode has no fabricated live connection. To explore the explicit sample
transport:

```bash
VITE_PLAY_FIXTURES=true pnpm dev
```

See the [frontend guide](docs/frontend.md) for supported Node versions, fixture
journeys, contract generation, and test commands.
