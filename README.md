# Wayfarer

A local-first, service-backed GURPS-inspired roleplaying engine with persistent campaigns, a responsive player UI, and an optional LLM game master. The engine validates characters and actions; generated prose never changes authoritative state.

## Setup and run

Install Python 3.14, [uv](https://docs.astral.sh/uv/), Node 22.22.2+ or 24.15.0+, and pnpm 11.19.0. From the repository root:

```bash
uv sync --frozen
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
```

Configure a separate access token for each player. Choose your own long random values; these authenticate players to your local service and are separate from an AI provider's credentials.

```bash
export WAYFARER_TOKENS='{"replace-with-your-own-long-random-token":"alice"}'
uv run --frozen wayfarer
```

Open [Wayfarer](http://127.0.0.1:8000). The normal command serves the production frontend and the authoritative `/setups` and `/api/v1` services together.

1. Select the **New game** tab, enter your access token, and select **Sign in**. The server identifies your player name; no campaign ID is required.
2. Setup runs as a stepper — **Concept**, **Adventure**, **Rules**, **Party** and **Ready** — showing one step at a time, with **Back** and **Next** moving between them. On **Adventure**, choose **The Last Beacon (solo)** under **Adventure and starting party**. Take **Next** to **Ready**, review the brief and the legal starting character there, then **Create game draft**.
3. The draft opens on **Party**. Assign Mira to your player name, then on **Ready** click **Validate and mark ready** followed by **Start game**.
4. The opening scene loads immediately, replacing the setup shell: the game shell starts at the top of every page. Use **Wait one tick** or **Travel to The Beacon** to play without an AI provider. Free-text interpretation and generated narration require a provider. The transcript records what you submitted and when; unsent text is kept as a draft for that scene and character alone, labelled with its age and discarded with **Discard draft**.

For multiplayer, add distinct token-to-player entries to `WAYFARER_TOKENS`, restart the server, and choose the two-player scenario. Invite the other player's name. They use the **Join game** tab and their own token to accept; the host assigns Mira and Iven, both players mark ready, and the host starts. Tokens are never shared between players.

**Session** in the play header reopens setup with **Switch campaign** (which returns to the campaign you were playing) or **New game**, and holds **End session**; setup and play are never shown at once. **Continue game** lists authorized saved games and unfinished setups. Every view is addressable per campaign — `/c/<campaign-id>/character`, for example — so a page can be bookmarked or shared, and a refresh returns to the same campaign and view. The access token is kept for that browser tab only: reloading restores the session, while closing the tab, leaving play, or **End session** discards what the tab would reopen. A pasted campaign link asks an unauthenticated visitor to sign in and then opens the view they asked for. Uncertain setup commands are retained in that tab's session storage under the authenticated player name; **Retry original setup request** resends the same command, including after refresh. Validation errors leave the draft editable; stale revisions require **Refresh this list**. Setup edits clear assignments and readiness.

SQLite saves campaigns and drafts under `data/wayfarer.sqlite3`; durable player-API receipts use `data/wayfarer.v1.sqlite3`. Back up both databases together. Restarting the server retains drafts and active play. You do not need seed scripts, fixtures, SQL, or pre-existing campaign IDs.

## Configuration

- `WAYFARER_HOST` and `WAYFARER_PORT`: default `127.0.0.1:8000`. `--port` overrides the port.
- `WAYFARER_DB`: campaign database path; `--db` overrides it.
- `WAYFARER_FRONTEND_DIR`: production build directory, default `frontend/dist` relative to the working directory. When launching an installed wheel outside the checkout, point this at the absolute path of your frontend build.
- `WAYFARER_ALLOWED_ORIGINS`: JSON array of permitted WebSocket origins. Defaults include localhost/127.0.0.1 on ports 8000 and 5173. Set it to the browser's actual origin when using another port or host.
- `WAYFARER_DATABASE_URL`: optional PostgreSQL connection string; the API receipt database still uses the configured local database path.

For frontend development, run `pnpm --dir frontend dev` alongside the Python service and open the URL Vite prints. Vite proxies `/setups`, `/campaigns`, and `/api/v1` (including WebSockets) to port 8000. Normal play does not use `VITE_PLAY_FIXTURES`.

For the playable two-player reference adventure, see [The Last Lantern](docs/wave-14.md): authored setup, investigation, negotiation, optional combat, capture/rescue, endings and continuation.

## Optional AI provider

The bundled scenario path always works without an LLM. For an API-backed provider:

```bash
export WAYFARER_OPENAI_API_KEY='your-key'
export WAYFARER_OPENAI_MODEL='your-structured-output-capable-model'
uv run --frozen wayfarer
```

For the Codex provider using a supported ChatGPT login, follow the [Codex setup guide](docs/wave-10.md). Configure `WAYFARER_LLM_PROVIDER=codex` before starting the service. Provider credentials stay on the server; each player still authenticates with their own Wayfarer access token.

## Documentation

See [architecture](docs/architecture.md), [UI onboarding](docs/ui-onboarding.md),
[unavailable states](docs/ui-availability.md), [guided scenario authoring](docs/scenario-authoring.md), [API runtime](docs/api-v1-runtime.md), [rules](docs/rules-catalog.md), [rules profiles](docs/rules-profiles.md), [persistence](docs/persistence.md), [testing](docs/testing.md), and [contributing](CONTRIBUTING.md).

This is a limited GURPS-inspired implementation, not a complete or officially licensed GURPS ruleset. No proprietary rulebook text is included.
