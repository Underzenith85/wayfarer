"""Authoritative recovery tasks on the existing shared clock and CAS store."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.recovery_types import ProfileId
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.medical import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
    RecoveryResult,
    apply_recovery,
)


@dataclass(frozen=True)
class CareEnvironment:
    technology_level: int = 8
    food: bool = False
    water: bool = False
    sleep: bool = False
    physician_id: str | None = None
    surgical_facility: bool = False
    anesthetic: bool = True
    surgical_modifier: int = 0


EnvironmentResolver = Callable[[PlayService, PlayState, str], CareEnvironment]


def _build(play: PlayService, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next((a for a in state.actors if a.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Recovery requires an approved character")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
    )
    return build


def _value(build: ValidatedBuild, key: str) -> int:
    value = next((v.value for v in build.sheet.values if v.target == key), None)
    if value is None or value != int(value):
        raise ValidationError("Required recovery skill or attribute is unavailable")
    return int(value)


class MedicalService:
    """Internal API; the environment resolver is bound by the scenario, not input.

    Starting or finishing care never moves the shared clock. Existing wait/party
    services determine elapsed time, and authoritative actions interrupt work.
    """

    def __init__(self, play: PlayService, environment: EnvironmentResolver) -> None:
        self.play, self.environment = play, environment

    async def execute(
        self, cid: str, command: BeginRecovery | FinishRecovery, *, authenticated_actor_id: str
    ) -> RecoveryResult:
        command = type(command).model_validate(command)
        if authenticated_actor_id != command.actor_id:
            raise ValidationError("Recovery actor does not match authenticated actor")
        play = self.play.for_campaign(await self.play.store.read(cid))
        profile_id = play.engine.reviewer.compiler.statistics_profile
        if profile_id not in ("gurps-lite-4e-2004", "gurps-basic-set-4e-2004"):
            raise ValidationError("Recovery requires an exact GURPS profile")
        selected = cast(ProfileId, profile_id)
        payload = json.dumps(
            {"operation": "gurps-recovery", "command": command.model_dump(mode="json")},
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> Event:
            before = play._load(campaign)
            task = next(
                (
                    t
                    for t in before.resources.recovery_tasks
                    if isinstance(command, FinishRecovery) and t.id == command.task_id
                ),
                None,
            )
            target_id = (
                command.target_id
                if isinstance(command, BeginRecovery)
                else task.target_id
                if task
                else ""
            )
            kind = command.kind if isinstance(command, BeginRecovery) else task.kind if task else ""
            if task is not None:
                context = CareContext(
                    task.profile_id,
                    task.ht,
                    task.skill,
                    task.technology_level,
                    task.food,
                    task.water,
                    task.sleep,
                    task.physician_skill,
                    task.physician_id,
                )
                resources, result = apply_recovery(
                    before.resources, command, context, rng=play.rng, system=True
                )
                updated = before.model_copy(
                    update={"revision": resources.revision, "resources": resources}
                )
                updated = play.checkpoint(updated, before=before)
                play.engine.validate(updated)
                campaign["revision"], campaign["play_json"] = (
                    updated.revision,
                    updated.model_dump_json(),
                )
                return Event(
                    input=payload,
                    action="recovery",
                    outcome=json.dumps({"task_id": result.task_id, "status": result.status}),
                    roll=None,
                )
            actor = _build(play, before, command.actor_id)
            target = _build(play, before, target_id)
            entities = {e.id: e for e in before.world.entities}
            if entities[command.actor_id].location_id != entities[target_id].location_id:
                raise ValidationError("Treatment requires the patient's location")
            if kind not in ("rest", "natural", "mortal-check"):
                hp = next(p for p in before.resources.pools if p.id == f"hp:{command.actor_id}")
                fp = next(p for p in before.resources.pools if p.id == f"fp:{command.actor_id}")
                if (
                    hp.injury is None
                    or hp.injury.incapacitated
                    or hp.injury.stunned
                    or (
                        fp.fatigue is not None
                        and (
                            fp.fatigue.collapsed
                            or fp.fatigue.unconscious
                            or fp.fatigue.heart_attack
                        )
                    )
                ):
                    raise ValidationError("Incapacitated characters cannot provide medical care")
            env = (
                CareEnvironment(
                    task.technology_level, task.food, task.water, task.sleep, task.physician_id
                )
                if task is not None
                else self.environment(play, before, target_id)
            )
            skill = (
                _value(actor, "skill:first-aid" if kind == "first-aid" else "skill:physician")
                if kind in ("first-aid", "physician")
                else None
            )
            treatment_modifier = 0
            if kind == "resuscitate":
                candidates = [
                    int(v.value) - (4 if v.target == "skill:first-aid" else 0)
                    for v in actor.sheet.values
                    if v.target in ("skill:first-aid", "skill:physician")
                    and v.value == int(v.value)
                ]
                if not candidates:
                    raise ValidationError("Resuscitation requires approved First Aid or Physician")
                skill = max(candidates)
            if kind == "stabilize":
                skill = _value(actor, "skill:surgery")
                _value(actor, "skill:physician")
                treatment_modifier = env.surgical_modifier - (0 if env.anesthetic else 2)
            physician = (
                _value(_build(play, before, env.physician_id), "skill:physician")
                if env.physician_id
                else None
            )
            if (
                env.physician_id
                and entities[env.physician_id].location_id != entities[target_id].location_id
            ):
                raise ValidationError("Physician must be present")
            if env.physician_id:
                physician_hp = next(
                    p for p in before.resources.pools if p.id == f"hp:{env.physician_id}"
                )
                physician_fp = next(
                    p for p in before.resources.pools if p.id == f"fp:{env.physician_id}"
                )
                if (
                    physician_hp.injury is None
                    or physician_hp.injury.incapacitated
                    or physician_hp.injury.stunned
                    or (
                        physician_fp.fatigue is not None
                        and (
                            physician_fp.fatigue.collapsed
                            or physician_fp.fatigue.unconscious
                            or physician_fp.fatigue.heart_attack
                        )
                    )
                ):
                    raise ValidationError("Physician must be capable of providing care")
            context = CareContext(
                selected,
                _value(target, "attribute:ht"),
                skill,
                env.technology_level,
                env.food,
                env.water,
                env.sleep,
                physician,
                env.physician_id,
                treatment_modifier,
                env.surgical_facility,
            )
            resources, result = apply_recovery(
                before.resources, command, context, rng=play.rng, system=True
            )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            updated = play.checkpoint(updated, before=before)
            play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            return Event(
                input=payload,
                action="recovery",
                outcome=json.dumps({"task_id": result.task_id, "status": result.status}),
                roll=None,
            )

        committed = await play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        state = play._load(committed["state"])
        # A retry reads the persisted result without re-running skills or conditions.
        event = next(e for e in state.resources.events if e.id == f"care:{command.id}")
        return RecoveryResult.model_validate_json(event.kind)
