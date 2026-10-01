"""Internal B57-58 commands using the shared transaction and private event stream."""

import json
from collections.abc import Callable
from typing import Annotated

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.traits.gadgeteer_gizmos import (
    PROFILE,
    GadgeteerGizmoApproval,
    craft_gizmo,
    gm_gizmo_rolls,
)
from wayfarer.engine.simulation.traits.gizmos import (
    BeginGizmoSession,
    GizmoOutcome,
    RevealGizmo,
    begin_session,
    history,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService

GizmoCommand = Annotated[BeginGizmoSession | RevealGizmo, Field(discriminator="kind")]
COMMAND_ADAPTER: TypeAdapter[GizmoCommand] = TypeAdapter(GizmoCommand)
ApprovalResolver = Callable[[PlayService, PlayState, RevealGizmo], GadgeteerGizmoApproval]


class GadgeteerGizmoService:
    """Only a trusted director can resolve an authored invention for an actor.

    No UI/API or profile certification is enabled here. The host binds a trusted
    approval resolver; a player command supplies only its stable eligibility ID.
    Materials, failed condition, injury and secret traces are one checkpoint.
    """

    def __init__(self, play: PlayService, resolve: ApprovalResolver) -> None:
        self.play, self.resolve = play, resolve

    def plan(
        self, play: PlayService, state: PlayState, command: GizmoCommand, *, principal_id: str
    ) -> CommandPlan[GizmoOutcome]:
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Gadgeteer Gizmos require the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "gadgeteer-gizmo",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            if isinstance(command, BeginGizmoSession):
                resources, result = begin_session(
                    before.resources, command, authorized_actor_id=command.actor_id, system=True
                )
            else:
                if not any(actor.actor_id == command.actor_id for actor in before.actors):
                    raise ValidationError("Gizmo requires an approved campaign actor")
                resources, result = craft_gizmo(
                    play.engine.resources,
                    before.resources,
                    command,
                    build(play.rules_context, before, command.actor_id),
                    play.engine.reviewer.compiler.definitions,
                    self.resolve(play, before, command),
                    rng=play.rng,
                    authorized_actor_id=command.actor_id,
                    system=True,
                )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> GizmoOutcome:
            resources = play._load(campaign).resources
            result = next((r for r in history(resources) if r.command_id == command.id), None)
            if result is None or (
                isinstance(command, RevealGizmo)
                and not any(
                    r.command_id == command.id
                    for r in gm_gizmo_rolls(resources, gm_authorized=True)
                )
            ):
                raise ConflictError("Gizmo canonical receipt is missing")
            return result

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Seats(state), Trusted(play.engine.reviewer.gm_ids)),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> GizmoOutcome:
        try:
            command = COMMAND_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid Gizmo command") from exc
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
