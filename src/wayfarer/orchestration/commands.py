"""Every campaign command family, registered rather than branched on (#635).

A family says how a raw command is parsed, what must already be true before it
runs, who may run it, and which service runs it. `CampaignRuntime.submit_json`
looks the family up; nothing under `orchestration/` asks what kind a command is.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wayfarer.contracts import Campaign, EventAction
from wayfarer.engine.simulation.actions import ACTION_ADAPTER, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.social_policy import parse_graph
from wayfarer.engine.simulation.health.fright_transitions import FrightDecision
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.adjudication import RULING_ADAPTER, AdjudicationService
from wayfarer.orchestration.combat import COMBAT_ADAPTER, CombatService
from wayfarer.orchestration.encounter_scenes import (
    EncounterSceneService,
    MigrateEncounterScenes,
)
from wayfarer.orchestration.fright import FrightService
from wayfarer.orchestration.fright_builds import (
    ApproveFrightBuild,
    FrightBuildService,
    ProposeFrightBuild,
)
from wayfarer.orchestration.membership import require_control
from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService
from wayfarer.orchestration.npcs import NPCProposal, NPCService
from wayfarer.orchestration.objectives import ObjectiveCommand, ObjectiveService
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.player_medical import PlayerRecoveryCommand
from wayfarer.orchestration.player_medical import execute as execute_medical
from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService
from wayfarer.orchestration.scenes import SCENE_ADAPTER, SceneService

if TYPE_CHECKING:
    from wayfarer.orchestration.medical import EnvironmentResolver


@dataclass(frozen=True)
class Submission:
    """One command, with the campaign facts every rule and service may need."""

    play: PlayService
    cid: str
    campaign: Campaign
    command: object
    state: PlayState
    member: CampaignMember
    principal_id: str
    medical_environment: EnvironmentResolver | None = None


Rule = Callable[[Submission], None]
Service = Callable[[Submission], Awaitable[None]]


def _actor_id(command: object) -> str:
    actor = getattr(command, "actor_id", None)
    if not isinstance(actor, str):
        raise ValidationError("Command does not name an actor")
    return actor


def _kind(command: object) -> str:
    kind = getattr(command, "kind", None)
    if not isinstance(kind, str):
        raise ValidationError("Command does not name a family")
    return kind


# --- Authorizers -----------------------------------------------------------
# Each raises on refusal. They compose, so a family states its rule instead of
# repeating a membership check inside the ladder that used to dispatch it.


def controls_actor(submission: Submission) -> None:
    require_control(submission.member, _actor_id(submission.command))


def service_authorizes(submission: Submission) -> None:
    """The family's service is the authority; membership adds no further rule.

    Registered rather than omitted so the table says which families delegate.
    """


def is_director(submission: Submission) -> None:
    if submission.member.role != "gm":
        raise AuthorizationError("This command requires director authority")


def director_controls_npc(submission: Submission) -> None:
    """A director acts for an NPC the pinned scenario declares, never for a player."""
    if submission.member.role != "gm":
        raise AuthorizationError("Acting for an NPC requires director authority")
    encoded = submission.campaign.get("scenario_graph_json")
    graph = parse_graph(encoded) if encoded is not None else None
    if graph is None or _actor_id(submission.command) not in graph.npc_actor_ids:
        raise AuthorizationError("Actor is not an authored NPC of this scenario")


@dataclass(frozen=True)
class All:
    rules: tuple[Rule, ...]

    def __init__(self, *rules: Rule) -> None:
        object.__setattr__(self, "rules", rules)

    def __call__(self, submission: Submission) -> None:
        for rule in self.rules:
            rule(submission)


@dataclass(frozen=True)
class Any_:
    rules: tuple[Rule, ...]

    def __init__(self, *rules: Rule) -> None:
        object.__setattr__(self, "rules", rules)

    def __call__(self, submission: Submission) -> None:
        refusal: Exception | None = None
        for rule in self.rules:
            try:
                rule(submission)
            except (AuthorizationError, ValidationError) as exc:
                refusal = refusal or exc
            else:
                return
        raise refusal or AuthorizationError("No authorization rule allowed this command")


# --- Preconditions ---------------------------------------------------------


def recovery_guard(submission: Submission) -> None:
    """The pending-mechanic guard the ladder applied to all but nine kinds."""
    guard(submission.state, _actor_id(submission.command), _kind(submission.command))


@dataclass(frozen=True)
class CommandFamily:
    kinds: tuple[str, ...]
    parse: Callable[[str], object]
    service: Service
    receipt: EventAction
    authorize: Rule
    preconditions: tuple[Rule, ...] = ()


async def _fright_build(submission: Submission) -> None:
    command = submission.command
    assert isinstance(command, ProposeFrightBuild | ApproveFrightBuild)
    await FrightBuildService(submission.play).execute(
        submission.cid, command, principal_id=submission.principal_id
    )


async def _fright_decision(submission: Submission) -> None:
    command = submission.command
    assert isinstance(command, FrightDecision)
    await FrightService(submission.play).execute(
        submission.cid, command, principal_id=submission.principal_id
    )


async def _encounter_scenes(submission: Submission) -> None:
    command = submission.command
    assert isinstance(command, MigrateEncounterScenes)
    await EncounterSceneService(submission.play).execute(
        submission.cid, command, principal_id=command.actor_id
    )


async def _player_recovery(submission: Submission) -> None:
    command = submission.command
    assert isinstance(command, PlayerRecoveryCommand)
    await execute_medical(
        submission.play,
        submission.state,
        command,
        controlled_actor_ids=submission.member.actor_ids,
        environment=submission.medical_environment,
    )


async def _ruling(submission: Submission) -> None:
    await AdjudicationService(submission.play).submit(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


async def _recovery(submission: Submission) -> None:
    await RecoveryService(submission.play).execute(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


async def _npc_proposal(submission: Submission) -> None:
    await NPCService(submission.play).propose(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


async def _combat(submission: Submission) -> None:
    await CombatService(submission.play).execute(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


async def _scene(submission: Submission) -> None:
    await SceneService(submission.play).execute(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


async def _noncombat(submission: Submission) -> None:
    await NoncombatService(submission.play).execute(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


async def _objective(submission: Submission) -> None:
    await ObjectiveService(submission.play).execute(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


async def _party(submission: Submission) -> None:
    await PartyService(submission.play).execute(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


async def _typed_action(submission: Submission) -> None:
    await submission.play.execute(
        submission.cid,
        submission.command,
        principal_id=_actor_id(submission.command),
    )


FAMILIES: tuple[CommandFamily, ...] = (
    CommandFamily(
        kinds=("propose_fright_build",),
        parse=ProposeFrightBuild.model_validate_json,
        service=_fright_build,
        receipt="npc",
        authorize=Any_(controls_actor, is_director),
    ),
    CommandFamily(
        kinds=("approve_fright_build",),
        parse=ApproveFrightBuild.model_validate_json,
        service=_fright_build,
        receipt="npc",
        authorize=Any_(controls_actor, is_director),
    ),
    CommandFamily(
        kinds=("care", "panic-response"),
        parse=FrightDecision.model_validate_json,
        service=_fright_decision,
        receipt="npc",
        authorize=service_authorizes,
    ),
    CommandFamily(
        kinds=("migrate_encounter_scenes",),
        parse=MigrateEncounterScenes.model_validate_json,
        service=_encounter_scenes,
        receipt="encounter-scenes",
        authorize=All(is_director, controls_actor),
        preconditions=(recovery_guard,),
    ),
    CommandFamily(
        kinds=("gurps_recovery",),
        parse=PlayerRecoveryCommand.model_validate_json,
        service=_player_recovery,
        receipt="recovery",
        authorize=controls_actor,
    ),
    CommandFamily(
        kinds=("request_ruling", "decide_ruling", "execute_ruling"),
        parse=RULING_ADAPTER.validate_json,
        service=_ruling,
        receipt="request_ruling",
        authorize=controls_actor,
        preconditions=(recovery_guard,),
    ),
    CommandFamily(
        kinds=("apply_setback", "choose_recovery"),
        parse=RecoveryCommand.model_validate_json,
        service=_recovery,
        receipt="recovery",
        authorize=controls_actor,
        preconditions=(recovery_guard,),
    ),
    CommandFamily(
        kinds=("propose_npc",),
        parse=NPCProposal.model_validate_json,
        service=_npc_proposal,
        receipt="npc",
        authorize=controls_actor,
        preconditions=(recovery_guard,),
    ),
    CommandFamily(
        kinds=("take_combat_turn", "resume_interrupted_turn", "choose_defense"),
        parse=COMBAT_ADAPTER.validate_json,
        service=_combat,
        receipt="combat",
        authorize=Any_(controls_actor, director_controls_npc),
    ),
    CommandFamily(
        kinds=(
            "start_encounter",
            "start_basic_encounter",
            "declare_basic_spatial_facts",
            "end_encounter",
            "join_encounter",
            "withdraw_encounter",
            "migrate_encounter_hex",
            "migrate_encounter_basic",
        ),
        parse=COMBAT_ADAPTER.validate_json,
        service=_combat,
        receipt="combat",
        authorize=Any_(controls_actor, director_controls_npc),
        preconditions=(recovery_guard,),
    ),
    CommandFamily(
        kinds=("observe_scene", "travel_scene"),
        parse=SCENE_ADAPTER.validate_json,
        service=_scene,
        receipt="scene",
        authorize=controls_actor,
        preconditions=(recovery_guard,),
    ),
    CommandFamily(
        kinds=("start_noncombat", "approach_noncombat", "withdraw_noncombat"),
        parse=NoncombatCommand.model_validate_json,
        service=_noncombat,
        receipt="noncombat",
        authorize=controls_actor,
        preconditions=(recovery_guard,),
    ),
    CommandFamily(
        kinds=("evaluate_objectives", "abandon_scenario"),
        parse=ObjectiveCommand.model_validate_json,
        service=_objective,
        receipt="objectives",
        authorize=controls_actor,
        preconditions=(recovery_guard,),
    ),
    CommandFamily(
        kinds=(
            "split_party",
            "rejoin_party",
            "queue_activity",
            "pause_group",
            "resume_group",
            "signal_scene",
            "transfer_item",
        ),
        parse=PartyCommand.model_validate_json,
        service=_party,
        receipt="party",
        authorize=controls_actor,
        preconditions=(recovery_guard,),
    ),
)

# Every kind the typed action union carries. It is the fallback family, so an
# unregistered kind still fails inside the engine's own discriminated union.
ACTIONS = CommandFamily(
    kinds=(),
    parse=ACTION_ADAPTER.validate_json,
    service=_typed_action,
    receipt="typed-action",
    authorize=controls_actor,
    preconditions=(recovery_guard,),
)

# `take_unarmed_turn` is a typed action, so it reaches the fallback family; the
# ladder exempted it from the guard by name and that exemption is kept here.
UNGUARDED_ACTIONS = frozenset({"take_unarmed_turn"})

BY_KIND: dict[str, CommandFamily] = {kind: family for family in FAMILIES for kind in family.kinds}


def family_for(kind: object) -> CommandFamily:
    """The family that owns this kind, or the typed-action family that owns the rest."""
    if isinstance(kind, str) and kind in BY_KIND:
        return BY_KIND[kind]
    if isinstance(kind, str) and kind in UNGUARDED_ACTIONS:
        return CommandFamily(
            kinds=ACTIONS.kinds,
            parse=ACTIONS.parse,
            service=ACTIONS.service,
            receipt=ACTIONS.receipt,
            authorize=ACTIONS.authorize,
        )
    return ACTIONS


def parse(kind: object, encoded: str) -> object:
    return family_for(kind).parse(encoded)


def kinds() -> Iterable[str]:
    return BY_KIND.keys()
