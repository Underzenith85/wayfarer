# Wave 10: subscription provider, NPC activity and recovery

Implements #30, #37 and #38 on the Wave 9 engine and merged API/UI baseline.
These are typed engine services and the existing authenticated campaign facade;
`/api/v1` conformance remains #50. No frozen contract or frontend changes.

## Codex subscription setup

`openai-codex` 0.147.0 and its matching bundled App Server runtime are locked in
`uv.lock`. The SDK supervises the process and owns credential storage/refresh.
Wayfarer never reads, copies or serializes Codex credential files or tokens.

Use a dedicated local profile with the installed helper:

```sh
uv sync --frozen
uv run wayfarer-codex-login
# For a headless machine:
uv run wayfarer-codex-login --device-auth
```

The helper uses the SDK equivalents of `codex login` and
`codex login --device-auth`. `WAYFARER_CODEX_HOME` selects the dedicated profile
(default `data/codex`). Existing CLI users can instead sign into that same profile
using Codex's login command. Do not copy another profile's credentials.

For an already constructed authoritative `PlayService`, enable the provider in
the existing campaign app composition:

```python
from aiohttp import web
from wayfarer.config import Settings
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.transport.campaign_api import create_campaign_app

# play and token_to_principal come from the trusted application composition.
app = create_campaign_app(
    CampaignAccess(play),
    token_to_principal,
    settings=Settings(llm_provider="codex"),
)
web.run_app(app, host="127.0.0.1", port=8000)
```

The old `wayfarer` demo remains a separate simplified prototype; this setting is
for the typed campaign app. New-game/scenario composition is owned by #39/#40.
`provider_runtime(Settings(...))` also exposes the same lifecycle to other hosts.
Switch to `llm_provider="responses"` with the existing API-key/model settings to
use the provider-independent Responses adapter.

Authenticated `POST /campaigns/{cid}/interpret` accepts `actor_id`, `command_id`
and `text`; it uses #20's scoped context, validated command dispatch and revision
checks. `GET /campaigns/{cid}/provider-status?actor_id=...` returns a bounded,
actor-session-scoped status buffer. It is ephemeral progress, not game history.
Raw provider text/reasoning/errors are never published as progress. The app closes
the process at shutdown; cancellation/timeouts interrupt turns and close it.

Settings: `WAYFARER_CODEX_MODEL` (default `gpt-5.6-terra`),
`WAYFARER_CODEX_EFFORT` (`low`, `medium`, `high`),
`WAYFARER_MODEL_TIMEOUT_SECONDS` (provider maximum 120 seconds),
`WAYFARER_CODEX_HOME`, and `WAYFARER_CODEX_SESSIONS` (opaque session/thread mapping
only). A generation change creates a new scoped session; a restart resumes the
same thread. Thread history is never authoritative game state.

The runtime uses an empty working directory, read-only sandbox, denied approval
requests, an environment allowlist, and disabled shell, exec, browser, computer,
image, plugin, app, MCP, memory and code-host capabilities. Its only game output
is untrusted schema-constrained JSON. Structured output cannot supply rolls or
mutate the database; the engine validates proposals again before committing.
Free-form SDK failures are replaced with fixed diagnostics without exception
chaining. Missing/expired login, usage limits, cancellation and timeouts have
separate typed errors. Narration failure preserves the committed game outcome.

Subscription mode is for one user or a trusted private deployment. It is not
public-service authentication or per-player billing. Transcription/synthesis and
Realtime Voice are not supplied by this login; voice transport remains #24.

Normal tests use contract fakes. A real process initialization with an empty
profile can verify startup without making a model call. To opt into a paid or
subscription-consuming authenticated interpretation/resume smoke test:

```sh
WAYFARER_CODEX_SMOKE=1 WAYFARER_CODEX_HOME=/absolute/dedicated/profile \
  uv run pytest tests/test_codex_provider.py -k authenticated_codex_smoke --no-cov
```

The smoke test skips without explicit opt-in or a suitable login. It is not a
normal CI requirement. Setup/API references:
[official SDK documentation](https://learn.chatgpt.com/docs/codex-sdk),
[App Server](https://learn.chatgpt.com/docs/app-server), and
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

## NPCs and faction clocks

Trusted `NPCRules` contain actor/faction identities, goals, dispositions, finite
plans, known-fact prerequisites, communication recipients, inventory costs,
start times, positive intervals and action/clock budgets. `NPCState` persists
pending decisions, optional bounded proposals, selected actions, exact known
facts, outcomes and progress. The deterministic policy chooses the first eligible
authored action if no usable proposal exists; it requires no model availability.
`propose_npc` is a GM/director-only command and can select only an authored option.
A stale/unknown-fact proposal cannot expand the option set or bypass costs.

Patrols, alarms and reinforcement signals advance plans and communicate only
facts already known to the NPC. Receiving a signal does not teleport a combatant:
physical reinforcement arrival still uses #45's arrival/encounter commands.
Prisoner transfers reference authored capture consequences and current custody.
NPCs are world actors and may have resource owners without being player-controlled
characters. A faction is a world entity; plan progress provides its actor's clock.

Shared-time ordering is resource schedules, existing objective boundary checks,
cross-scene signals, queued activities in subgroup/command order, then NPC actions
in due-time/plan order and outcome settlement. Positive intervals and total finite
budgets bound loops. A transfer cannot bypass unresolved combat defense or an
in-progress subgroup activity. Committed decisions replay from stored events;
there are no model calls or player waits inside transactions.

## Setbacks, capture and downtime

GM `apply_setback` selects a pinned `SetbackRule` and authorized target. Supported
consequences are retreat, surrender/capture, incapacitation and death. A rule
provides its evidence, location, captor, custody owner, restraints, permitted
choices and explicit consequence/failure facts. Capture does not itself mark
objectives impossible or end the campaign. Authored impossible-objective IDs and
failure evidence connect unrecoverable setbacks to the objective evaluator.

Capture splits the target into a subgroup without stopping free players. Inventory
is unequipped, unpacked leaf-first and transferred using resource commands within
the same transaction. Every item retains its identity; failed custody validation
rolls the whole command back. Custody history survives recapture and transfer.
Same-location capture refreshes combat readiness without erasing the encounter;
authored retreat/death or departure closes the affected encounter at a resolved
boundary. Pending defenses must resolve first.

Player `choose_recovery` selects a pinned option, never arbitrary effects. Options
include observation, communication, negotiation, inside assistance, escape,
external rescue, gear recovery, rest, resupply, advancement and replacement. They validate
actor/target authority, location, actor-known evidence, restraints, current status,
implemented checks/equipment and explicit costs. Unsupported choices produce a
persisted `adjudication_required` decision with no mechanics applied; they need a
separately supported GM adjudication rather than invented escape mechanics.

Choices persist as pending shared-time activities and revalidate when due. Dice
traces, success/failure/rejection and costs are saved atomically. A failed check
can reveal authored failure consequences. Rescuers must physically reach the
scene and have evidence; one target can be rescued while another remains held.
Rescue/inside assistance can resolve during combat between pending defenses.
Other captures/deaths cannot bypass restrictions through ordinary actions,
travel, combat, inventory transfer or subgroup rejoin.

Release changes freedom only. Recovering gear is a separate authored option and
requires reachable recorded custody; spent or moved gear is not recreated. Communication uses explicit recipient routes and can deliver only facts known to
the sender. Rest
uses authored time, bounded HP/FP recovery and consumable costs. Resupply transfers
finite existing stock. NPC schedules continue while shared time advances.
Replacement uses an authored build through the existing legality compiler and
automatic power policy, records its predecessor and retires the predecessor's
unspent advancement balance. The controller/actor slot stays stable; there is no
free point or equipment grant. Nonautomatic replacements require separately
approved policy. Downtime advancement invokes the same pure purchase reducer as
`AdvancementService`, retaining compile, power-review, earned-point and build
revision gates inside the shared-time transaction. Refunds remain GM-authorized.

## Verification

Feature contracts cover restart/replay, duplicate and conflicting commands,
concurrent capture retries, custody rollback, captive actions, failed/partial
escape, two authenticated players rescuing and reuniting without secret sharing,
rescue during combat, finite stock, rest costs, death/legal and illegal replacement,
explicit mission failure, NPC unknown-fact fallback, action budgets and transfers.
The capture/rescue storage contract also runs against PostgreSQL in CI.
Provider fakes exercise thread resume/scope, structured output, actual SDK event
models, typed failures, timeout/cancellation, process cleanup, forged rolls,
post-commit narration failure and HTTP status authorization.

All mechanics are authored prototype policies over implemented rules. This does
not claim comprehensive published GURPS injury, healing, prison or social rules,
autonomous tactical NPC combat, the full scenario studio, or the Wave 11 director.
