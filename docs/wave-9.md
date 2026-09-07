# Wave 9: resolution, objectives, encounters, orchestration and concurrent scenes

Implements roadmap issues #17, #20, #35, #36 and #45 on the merged Wave 8
foundations. The server owns rules, checks, costs, inventory, clocks and outcomes.
All commands use the existing campaign transaction, optimistic revision, durable
command receipt, event log and replay checkpoint. Model calls happen outside locks.

## Combat (#17)

`CombatRules.attacks` and `.protection` bind authored profiles to implemented,
pinned equipment definitions. The declared original prototype subset supports
ready melee weapons, torso targeting, DX plus equipment effects and posture,
one active dodge/parry, 3d6 checks, critical-hit defense bypass, rolled basic
damage, strongest equipped protection, integer injury multipliers, HP clamping,
incapacitation and bounded recovery time. Traces retain actual attack/defense
rolls, derived values, damage dice, armor and before/after HP. Defense choice and
injury commit together, so retrying cannot apply damage again.

This is not full published GURPS. Ranged modes, hit locations other than torso,
blocking, bleeding, death checks, special critical tables and other unconfigured
mechanics are unsupported. Empty profiles retain the explicitly non-damaging
Wave 8 lifecycle configuration and its digest; enabling profiles changes the
configuration pin. A configured weapon is required for damaging resolution.

## Objectives and rewards (#35)

Authored `ObjectiveRules` use a finite predicate vocabulary: fact equality,
actor knowledge, inventory quantity, condition, location, custody, game time and
scheduled-event evidence. Negation allows freedom/capture goals without treating
one captured actor as total party defeat. No predicate evaluates generated code.
Required and optional objectives have per-actor visibility. Predicate references
and contradictions are validated before play.

Every implemented gameplay commit evaluates a checkpoint. Deadline crossings
use the pre-completion state at the deadline: late progress cannot retroactively
satisfy it. Tie order is explicit failure, deadline, explicit abandonment, then
required-objective success. A deadline can yield partial success if some prior
objective was satisfied. Terminal outcomes are immutable and do not terminate
or archive the campaign. An authenticated GM can explicitly abandon a scenario.

Settlement is in the same transaction: earned points enter the advancement
ledger; item rewards transfer existing escrow items through ResourceEngine.
Invalid capacity/custody/equipment causes the whole transaction to roll back.
Stable reward IDs prevent duplicate awards across retries and replay. This is
scenario completion, not campaign termination; continuation belongs to Wave 13.

## Noncombat encounters (#36)

`NoncombatRules` provide negotiation, investigation, infiltration, pursuit and
hazard fixtures. Each authored approach references an existing pinned check,
progress, FP/time costs and success/failure consequences. Failures can advance
progress and reveal an alternate route; exhausted failure stakes take precedence
over simultaneous progress. Player selection, history, withdrawal and remaining
choices persist. Unsupported selections mutate nothing. NPC behavior and rescue
recovery are downstream integrations, not model-authored mechanics here.

## Split parties and time (#45)

Subgroups retain actor membership, scene, generation and a ready-through clock
in one canonical world. Existing scene cursors migrate structurally when party
coordination is enabled, without advancing time or copying knowledge/resources.
Split and rejoin move only the requesting actor. Rejoin requires the same scene
and a synchronization barrier. Players cannot control another principal's actors.

`queue_activity` accepts bounded typed checks, waits, item use, chosen noncombat
approaches and scene travel. Duration is calculated from server rules. A subgroup
can have one unresolved activity; the queue captures subgroup generation and actor
identity, then revalidates feasibility at execution. Failed revalidation produces
a scoped rejection receipt without spending resources or applying the action.
Ordinary immediate time-advancing APIs cannot bypass a split-party queue.

The committed clock is the minimum subgroup ready-through time. Activity effects
wait until that frontier reaches their effective time. Simultaneous durations
therefore overlap rather than add. At a given time, scheduled resource effects
and deadlines precede cross-scene effects, followed by activities ordered by
subgroup ID and command ID. Each combat round advances its subgroup one prototype
tick. Pending defense choices hold that group's frontier, preventing a long
investigation from committing past unresolved combat. Other groups can still
submit activity or manage their independent choices.

Authored cross-scene signals reveal a specified fact only to configured actors at
the effective shared time. `join_encounter` admits an arrived, synchronized actor
between resolved combat stages; a pending attack must resolve first. Arrival
atomically reconciles subgroup membership, initiative and participants, validating
terrain and occupancy. No secret knowledge is copied on reunion.

Disconnect does not choose actions. Accepted activity remains durable; an idle
controller holds its time boundary. Explicit pause/resume affects the subgroup.
Automatic consequential defaults and live controller reassignment are disabled;
host membership remains a trusted campaign setup concern. Resources transfer only
between co-located, synchronized actors outside combat/pending activity.

## Authenticated API and LLM boundary (#19/#20)

These are engine adapter endpoints, not a claim of frozen `/api/v1` conformance.
The parallel #50 backend adapter owns the v1 envelopes, opaque visible-resource
versions and opaque stream cursors; this legacy facade still exposes campaign
revision counters. The frozen contract and frontend remain unchanged.

The existing `POST /campaigns/{cid}/commands` routes combat, scenes, objectives,
noncombat and party commands under bearer-authenticated campaign membership.
Projections expose controlled actor inventory/status/pools, subgroup activity,
noncombat decisions and visible objective progress. Streams redact other actors'
command IDs, identities and outcomes. Reconnection uses the durable revision
cursor; optimistic conflicts require the caller to refresh rather than silently
rebase a consequential choice.

`StructuredProvider` exposes structured intent, character/scenario draft and
narration operations. `ResponsesProvider` bridges the existing Responses client;
Codex subscription integration remains #30. Runtime schemas forbid forged costs,
rolls, identities and state. The orchestrator binds identity and revision itself,
rechecks permission/session generation after model calls, and executes through
CampaignAccess. Session identity includes campaign, principal, actor, subgroup and
generation. Context is perspective-filtered with bounded recent event metadata;
raw canonical event payloads and hidden objective evidence are never retrieved
into player/NPC model contexts. Drafts are proposals only; activation still uses
compiler and approval services.

Provider requests have timeouts, bounded retries, response/context limits and usage
accounting. Cancellation propagates. Telemetry contains operation/status/counts,
not prompts, secrets or exception bodies. Narration uses a committed outcome and
visible projection. Narration failure returns that authoritative result without
undoing the command. This layer does not implement the persistent director (#39)
or player-facing workshop/studio UI (#21/#22).

## Verification

Feature fixtures exercise injury/retry/replay, deadline boundaries and immutable
settlement, all noncombat categories, failed checks with continued progress,
independent choices, authenticated split/rejoin, shared-time alarms, queued travel,
combat versus investigation, reinforcement arrival, private knowledge, provider
forgery, stale proposals, timeout, cancellation and failed narration. Existing
SQLite/PostgreSQL transaction tests on the Python 3.14 CI target remain mandatory. See the PR for the final test counts and CI status.
