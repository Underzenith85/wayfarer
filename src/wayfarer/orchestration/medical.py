"""Authoritative recovery tasks on the existing shared clock and CAS store."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.entropy import commit_command
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
    life_support: bool = False
    sterile: bool = True
    equipment_quality_modifier: int = 0
    infection_risk: bool = False
    infection_modifier: int = 0


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


def care_skill(actor: ValidatedBuild, kind: str, env: CareEnvironment) -> tuple[int | None, int]:
    """Select the caregiver's effective skill and treatment modifier for one procedure.

    B424: resuscitation accepts First Aid at -4 or Physician; stabilising requires
    Surgery backed by Physician and suffers -2 without anesthetic on top of the
    surgical environment. Kinds without a roll return no skill.
    """
    if kind in ("first-aid", "physician"):
        return _value(actor, "skill:first-aid" if kind == "first-aid" else "skill:physician"), 0
    if kind == "resuscitate":
        candidates = [
            int(v.value) - (4 if v.target == "skill:first-aid" else 0)
            for v in actor.sheet.values
            if v.target in ("skill:first-aid", "skill:physician") and v.value == int(v.value)
        ]
        if not candidates:
            raise ValidationError("Resuscitation requires approved First Aid or Physician")
        return max(candidates), 0
    if kind == "stabilize":
        skill = _value(actor, "skill:surgery")
        _value(actor, "skill:physician")
        return skill, env.surgical_modifier - (0 if env.anesthetic else 2)
    return None, 0


def care_context(
    profile: ProfileId,
    actor: ValidatedBuild,
    target: ValidatedBuild,
    kind: str,
    env: CareEnvironment,
    physician: int | None,
) -> CareContext:
    skill, modifier = care_skill(actor, kind, env)
    if kind == "trauma-maintenance":
        physician = _value(actor, "skill:physician")
    return CareContext(
        profile,
        _value(target, "attribute:ht"),
        skill,
        env.technology_level,
        env.food,
        env.water,
        env.sleep,
        physician,
        env.physician_id,
        modifier,
        env.surgical_facility,
        surgery_skill=_value(actor, "skill:surgery")
        if kind in ("repair-lasting", "repair-permanent")
        else None,
        life_support=env.life_support,
        sterile=env.sterile,
        anesthetic=env.anesthetic,
        equipment_quality_modifier=env.equipment_quality_modifier,
        infection_risk=env.infection_risk,
        infection_modifier=env.infection_modifier,
    )


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

        def resolve(campaign: Campaign) -> CommandReceipt:
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
                play.commit(campaign, updated)
                return CommandReceipt(
                    action="recovery",
                    outcome=json.dumps({"task_id": result.task_id, "status": result.status}),
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
            context = care_context(selected, actor, target, kind, env, physician)
            resources, result = apply_recovery(
                before.resources, command, context, rng=play.rng, system=True
            )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(
                action="recovery",
                outcome=json.dumps({"task_id": result.task_id, "status": result.status}),
            )

        committed = await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=play.rng,
        )
        state = play._load(committed["state"])
        # A retry reads the persisted result without re-running skills or conditions.
        event = next(e for e in state.resources.events if e.id == f"care:{command.id}")
        return RecoveryResult.model_validate_json(event.kind)
