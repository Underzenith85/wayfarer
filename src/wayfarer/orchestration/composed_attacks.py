"""Private approved attacks on the canonical campaign CAS, turn and replay path."""

import json

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.abandon import AbandonPendingAttack, abandon
from wayfarer.engine.simulation.combat.attack_visibility import private_attack_result
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.traits.attack_defense import TraitAttackOutcome
from wayfarer.engine.simulation.traits.composed_host import (
    ADAPTER,
    AbandonComposedAttack,
    ComposedCommand,
    ContinueComposedCritical,
    ResistComposedAttack,
    UseComposedAttack,
    declare,
)
from wayfarer.engine.simulation.traits.composed_resolution import (
    RESOLUTION_PREFIX,
    ComposedResolution,
    finish,
    resolve,
)
from wayfarer.engine.simulation.traits.composed_sources import (
    BindComposedSource,
    bind,
    finish_binding,
    identity,
    pending_binding,
)
from wayfarer.engine.simulation.traits.innate_criticals import (
    InnateAdjudication,
    continue_innate_miss,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.combat.context import CombatContext, CombatStep, encounter_for
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat
from wayfarer.orchestration.combat.steps import reduce_combat
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.opponent_attack_privacy import (
    may_view_attack,
    preserve_attack_visibility,
    visible_combat_result,
)
from wayfarer.orchestration.pipeline import CommandPlan, Control, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import generation, replay_payload

PREFIX = "composed-result:"


class ComposedResult(Record):
    command_id: str
    source_id: str | None = None
    combat: CombatResult | None = None
    attack: TraitAttackOutcome | None = None


def _controls(
    play: PlayService, state: PlayState, actor_id: str, principal_id: str, *, director: bool = False
) -> tuple[Control, ...]:
    member = member_for(state, principal_id)
    if director or member.role == "gm":
        return Seats(state), Trusted(play.engine.reviewer.gm_ids)
    return (Controls(member, actor_id),)


async def recorded_operation(play: PlayService, cid: str, command_id: str, operation: str) -> bool:
    prior = await play.store.command_input(cid, command_id)
    if prior is None or prior.text is None:
        return False
    generation(prior)  # Verify original bytes and metadata before interpreting its family.
    payload = replay_payload(prior.text)
    return isinstance(payload, dict) and payload.get("operation") == operation


class ComposedAttackService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, play: PlayService, state: PlayState, command: ComposedCommand, *, principal_id: str
    ) -> CommandPlan[ComposedResult]:
        payload = json.dumps(
            {
                "operation": "composed-attack",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            if before.revision != command.expected_revision or before.lifecycle != "active":
                raise ConflictError("Composed operation requires the current active play revision")
            updated = before
            result = ComposedResult(command_id=command.id)
            if isinstance(command, BindComposedSource):
                updated, source = bind(play.rules_context, before, command, principal_id)
                revision = before.revision + 1
                updated = updated.model_copy(
                    update={
                        "revision": revision,
                        "resources": updated.resources.model_copy(update={"revision": revision}),
                    }
                )
                result = result.model_copy(update={"source_id": source.id})
            else:
                encounter = encounter_for(before, command.encounter_id)
                context = CombatContext(play, before)
                if isinstance(command, UseComposedAttack):
                    updated, encounter, combat = declare(
                        play.rules_context, before, encounter, command
                    )
                    result = result.model_copy(update={"source_id": command.source_id})
                elif isinstance(command, ResistComposedAttack):
                    resolved = resolve(play.rules_context, before, encounter, command)
                    updated, encounter, combat = resolved.state, resolved.encounter, resolved.result
                    result = result.model_copy(update={"attack": resolved.outcome})
                elif isinstance(command, AbandonComposedAttack):
                    pending = encounter.pending_defense
                    if pending is None or pending.composed_attack_id is None:
                        raise ConflictError("No composed attack awaits abandonment")
                    binding = pending_binding(before.resources, encounter, pending)
                    updated, encounter, combat = abandon(
                        play.rules_context,
                        before,
                        encounter,
                        AbandonPendingAttack(
                            id=command.id,
                            actor_id=command.actor_id,
                            expected_revision=command.expected_revision,
                            encounter_id=command.encounter_id,
                            pending_id=command.pending_id,
                        ),
                    )
                    updated = updated.model_copy(
                        update={
                            "resources": finish_binding(
                                updated.resources, binding, "abandoned:" + command.id
                            )
                        }
                    )
                    actor = next(
                        p for p in encounter.participants if p.actor_id == command.actor_id
                    )
                    encounter = context.engine._replace(
                        encounter,
                        actor.model_copy(
                            update={
                                "maneuver_state": actor.maneuver_state.model_copy(
                                    update={"concentrating": False, "concentration_seconds": 0}
                                )
                            }
                        ),
                    )
                else:
                    pending = encounter.pending_defense
                    if pending is None or pending.id != command.pending_id:
                        raise ConflictError("Composed critical pending identity changed")
                    binding = pending_binding(before.resources, encounter, pending)
                    saved = next(
                        (
                            ComposedResolution.model_validate_json(e.kind)
                            for e in reversed(before.resources.events)
                            if e.id.startswith(RESOLUTION_PREFIX)
                            and ComposedResolution.model_validate_json(e.kind).critical_id
                            == command.critical_id
                        ),
                        None,
                    )
                    if saved is None or saved.binding_id != binding.id:
                        raise ConflictError("Composed critical has no captured delivery")
                    resources, encounter, _ = continue_innate_miss(
                        before.resources,
                        encounter,
                        critical_id=command.critical_id,
                        command_id=command.id,
                        context_digest=command.context_digest,
                        adjudication=InnateAdjudication(
                            principal_id=principal_id,
                            policy_id=command.policy_id,
                            reason=command.reason,
                            effect=command.effect,
                            duration_seconds=command.duration_seconds,
                        ),
                        rng=play.rng,
                        system=True,
                    )
                    updated = before.model_copy(update={"resources": resources})
                    updated, encounter, combat = finish(
                        play.rules_context,
                        updated,
                        encounter,
                        binding,
                        saved.selected,
                        command.id,
                        saved.trace.model_copy(update={"adjudication_required": None}),
                    )
                    result = result.model_copy(update={"attack": saved.outcome})
                step = CombatStep(updated, encounter, updated.resources, combat)
                encounters = tuple(
                    encounter if e.id == encounter.id else e for e in updated.encounters
                )
                step, encounters = _settle_combat(step, command, encounters, context)
                updated, combat = _finish_combat(step, command, encounters, context)
                result = result.model_copy(update={"combat": combat})
                updated = preserve_attack_visibility(
                    before, updated, command.encounter_id, command.id
                )
            event = ResourceEvent(
                id=identity(PREFIX, command.id),
                at=updated.resources.game_time,
                target_id=command.actor_id,
                kind=result.model_dump_json(),
            )
            updated = updated.model_copy(
                update={
                    "resources": updated.resources.model_copy(
                        update={"events": updated.resources.events + (event,)}
                    )
                }
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="composed:" + command.kind)

        async def outcome(campaign: Campaign) -> ComposedResult:
            resources = play._load(campaign).resources
            event = next(
                (e for e in resources.events if e.id == identity(PREFIX, command.id)), None
            )
            if event is None:
                raise ValidationError("Missing committed composed attack outcome")
            return ComposedResult.model_validate_json(event.kind)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=reduce,
            actor_id=principal_id,
            outcome=outcome,
            control=_controls(
                play,
                state,
                command.actor_id,
                principal_id,
                director=isinstance(command, (BindComposedSource, ContinueComposedCritical)),
            ),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> ComposedResult:
        try:
            command = ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid private composed attack command") from exc
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        state = play._load(campaign)
        result = await submit(
            play,
            cid,
            self.plan(play, state, command, principal_id=principal_id),
            principal_id=principal_id,
        )
        current = play._load(await play.store.read(cid))
        if private_attack_result(current.resources, command.id) and not may_view_attack(
            play, current, command.id, command.actor_id, principal_id
        ):
            result = result.model_copy(
                update={
                    "attack": None,
                    "combat": result.combat.model_copy(update={"injury": None, "unarmed": None})
                    if result.combat is not None
                    else None,
                }
            )
        return result

    async def defend(self, cid: str, command: ChooseDefense, *, principal_id: str) -> CombatResult:
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        state = play._load(campaign)
        payload = json.dumps(
            {
                "operation": "composed-defense",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            encounter = encounter_for(before, command.encounter_id)
            pending = encounter.pending_defense
            if pending is None or pending.composed_attack_id is None:
                raise ConflictError("No composed attack awaits this defense")
            updated, result = reduce_combat(before, command, CombatContext(play, before))
            updated = preserve_attack_visibility(before, updated, command.encounter_id, command.id)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="combat", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> CombatResult:
            # A receipt remains this command's outcome after later turns and restart.
            record = next(
                (r for r in await play.store.history(cid) if r.command_id == command.id), None
            )
            if record is None:
                raise ValidationError("Missing composed defense receipt")
            return CombatResult.model_validate_json(record.event["outcome"])

        plan = CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=reduce,
            actor_id=principal_id,
            outcome=outcome,
            control=_controls(play, state, command.actor_id, principal_id),
            rng=play.rng,
        )
        result = await submit(play, cid, plan, principal_id=principal_id)
        current = play._load(await play.store.read(cid))
        return visible_combat_result(
            play, current, result, command.id, command.actor_id, principal_id
        )
