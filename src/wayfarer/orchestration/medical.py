"""Authoritative recovery tasks on the existing shared clock and CAS store."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, replace
from typing import Literal, cast

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.rules.magic.healing import RECOVER_ENERGY
from wayfarer.engine.rules.magic.healing import package as healing_package
from wayfarer.engine.rules.types.care_environment import CareEnvironment as CareEnvironment
from wayfarer.engine.rules.types.care_environment import captured as captured_environment
from wayfarer.engine.rules.types.care_minimum import KINDS as MINIMUM_KINDS
from wayfarer.engine.rules.types.recovery import ProfileId
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.default_knowledge import (
    PROFILE as DEFAULT_KNOWLEDGE_PROFILE,
)
from wayfarer.engine.simulation.campaign.default_knowledge import adopted
from wayfarer.engine.simulation.campaign.default_knowledge_projection import (
    project as project_default_knowledge,
)
from wayfarer.engine.simulation.health.medical import minimum
from wayfarer.engine.simulation.health.medical.commands import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
    RecoveryResult,
)
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.enchanting_calendar import require_enchanting_free
from wayfarer.engine.simulation.magic.spell_state import active_spells
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.ability_boundary import require_boundary
from wayfarer.orchestration.medical_bandage_generations import capture as capture_bandage
from wayfarer.orchestration.medical_minimum_generations import capture as capture_minimum
from wayfarer.orchestration.medical_rest_generations import ENVIRONMENT as REST_ENVIRONMENT
from wayfarer.orchestration.medical_rest_generations import KEY as REST_KEY
from wayfarer.orchestration.medical_rest_generations import capture as capture_rest
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.medical_bandage_inputs import stamp as stamp_bandage
from wayfarer.persistence.medical_minimum_inputs import stamp as stamp_minimum


def _rest_command(state: PlayState, command: BeginRecovery | FinishRecovery) -> bool:
    return (
        command.kind == "rest"
        or isinstance(command, FinishRecovery)
        and any(
            task.id == command.task_id and task.kind == "rest"
            for task in state.resources.recovery_tasks
        )
    )


def _require_rest_interval(state: PlayState, command: BeginRecovery | FinishRecovery) -> None:
    if not _rest_command(state, command):
        raise ValidationError("Captured FP-rest consumer cannot execute another medical procedure")
    if isinstance(command, BeginRecovery):
        require_enchanting_free(
            state.resources, command.actor_id, through=state.resources.game_time + command.seconds
        )


def _decode_rest_environment(raw: object) -> CareEnvironment:
    if not isinstance(raw, dict) or set(raw) != {field.name for field in fields(CareEnvironment)}:
        raise ValidationError("Recorded FP-rest environment fields are incomplete or unknown")
    return captured_environment(raw)


def _decode_minimum_environment(raw: object) -> CareEnvironment:
    if not isinstance(raw, dict) or set(raw) != {field.name for field in fields(CareEnvironment)}:
        raise ValidationError("Recorded medical environment fields are incomplete or unknown")
    return captured_environment(raw)


EnvironmentResolver = Callable[[PlayService, PlayState, str], CareEnvironment]

CareAdmission = Callable[[PlayService, PlayState, BeginRecovery], None]


@dataclass(frozen=True)
class CarePolicy:
    """Optional deployment admission; the ordinary resolver contract is unchanged.

    Admission is pure. Begin plans assess it after saved-receipt lookup and
    before entropy, then recheck the actual transaction prestate. Finishes use
    the already reserved task. The policy never supplies a skill or outcome.
    """

    resolve: EnvironmentResolver
    admit: CareAdmission
    capture_bandage_environment: bool = False

    def __call__(self, play: PlayService, state: PlayState, target_id: str) -> CareEnvironment:
        return self.resolve(play, state, target_id)


def admit_environment(
    environment: EnvironmentResolver,
    play: PlayService,
    state: PlayState,
    command: BeginRecovery,
) -> None:
    if isinstance(environment, CarePolicy):
        environment.admit(play, state, command)


def _build(play: PlayService, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next((a for a in state.actors if a.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Recovery requires an approved character")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
    )
    if play.rules_context.correct_default_knowledge:
        return project_default_knowledge(
            play.engine.reviewer.compiler,
            state,
            actor_id,
            build,
            required=play.rules_context.require_default_knowledge,
        )
    return build


def _value(build: ValidatedBuild, key: str) -> int:
    value = next((v.value for v in build.sheet.values if v.target == key), None)
    if value is None or value != int(value):
        raise ValidationError("Required recovery skill or attribute is unavailable")
    return int(value)


def _require_presence(state: PlayState, actor_id: str, target_id: str) -> None:
    entities = {e.id: e for e in state.world.entities}
    if entities[actor_id].location_id != entities[target_id].location_id:
        raise ValidationError("Treatment requires the patient's location")


def _require_available(state: PlayState, actor_id: str, kind: str) -> None:
    if kind in {"rest", "natural", "mortal-check"}:
        return
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    if (
        hp.injury is None
        or hp.injury.incapacitated
        or hp.injury.stunned
        or (
            fp.fatigue is not None
            and (fp.fatigue.collapsed or fp.fatigue.unconscious or fp.fatigue.heart_attack)
        )
    ):
        raise ValidationError("Incapacitated characters cannot provide medical care")


def _require_default_care(
    play: PlayService, state: PlayState, actor_id: str, actor: ValidatedBuild, kind: str
) -> None:
    # Presence has already been projected under the actor's new policy. The two
    # direct skill treatments can refuse before asking for any environment facts.
    selected = {"first-aid": "skill:first-aid", "physician": "skill:physician"}.get(kind)
    if (
        play.rules_context.correct_default_knowledge
        and play.engine.reviewer.compiler.statistics_profile == DEFAULT_KNOWLEDGE_PROFILE
        and (play.rules_context.require_default_knowledge or adopted(state, actor_id))
        and selected is not None
    ):
        _value(actor, selected)


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
            int(v.value)
            - (env.resuscitation_first_aid_penalty if v.target == "skill:first-aid" else 0)
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
        drug_item_id=env.drug_item_id,
        drug_form=env.drug_form,
        drug_hp=env.drug_hp,
        drug_fp=env.drug_fp,
    )


def _recovery_payload(
    selected: ProfileId,
    command: BeginRecovery | FinishRecovery,
    *,
    rest_admission: bool,
    rest_environment: CareEnvironment | None,
    minimum_admission: bool,
    minimum_environment: CareEnvironment | None,
    bandage_admission: bool,
    bandage_environment: CareEnvironment | None,
) -> str:
    """Encode the exact producer bytes for independent medical capture policies."""
    if rest_environment is not None and not rest_admission:
        raise ValidationError("Captured FP-rest facts require their generation")
    if minimum_admission and (
        selected != "gurps-basic-set-4e-2004"
        or not isinstance(command, BeginRecovery)
        or command.kind not in MINIMUM_KINDS
    ):
        raise ValidationError("Medical minimum admission requires its voluntary start")
    if minimum_admission != (minimum_environment is not None):
        raise ValidationError("Medical minimum policy requires its captured environment")
    if rest_admission and (
        selected != "gurps-basic-set-4e-2004"
        or isinstance(command, BeginRecovery)
        and (command.kind != "rest" or rest_environment is None)
    ):
        raise ValidationError("Captured FP-rest admission requires its exact supported producer")
    if bandage_admission and (
        selected != "gurps-basic-set-4e-2004"
        or isinstance(command, BeginRecovery)
        and command.kind != "bandage"
        or isinstance(command, BeginRecovery) != (bandage_environment is not None)
    ):
        raise ValidationError("Bandage capture requires its independent supported producer")
    if not bandage_admission and bandage_environment is not None:
        raise ValidationError("Bandage context requires its captured generation")
    payload = json.dumps(
        {
            "operation": "gurps-recovery",
            "command": command.model_dump(mode="json"),
            **({REST_KEY: 1} if rest_admission else {}),
            **(
                {REST_ENVIRONMENT: asdict(rest_environment)} if rest_environment is not None else {}
            ),
        },
        sort_keys=True,
    )
    if minimum_admission:
        assert minimum_environment is not None
        payload = stamp_minimum(payload, asdict(minimum_environment))
    if bandage_admission:
        payload = stamp_bandage(
            payload, asdict(bandage_environment) if bandage_environment is not None else None
        )
    return payload


class MedicalService:
    """Internal API; the environment resolver is bound by the scenario, not input.

    Starting or finishing care never moves the shared clock. Existing wait/party
    services determine elapsed time, and authoritative actions interrupt work.
    """

    def __init__(self, play: PlayService, environment: EnvironmentResolver) -> None:
        self.play, self.environment = play, environment

    def plan(
        self,
        play: PlayService,
        selected: ProfileId,
        command: BeginRecovery | FinishRecovery,
        *,
        principal_id: str,
        rest_admission: bool = False,
        rest_environment: CareEnvironment | None = None,
        minimum_admission: bool = False,
        minimum_environment: CareEnvironment | None = None,
        admission_state: PlayState | None = None,
        bandage_admission: bool = False,
        bandage_environment: CareEnvironment | None = None,
    ) -> CommandPlan[RecoveryResult]:
        """What a care command writes; the pipeline decides whether it runs."""
        policy_begin = isinstance(self.environment, CarePolicy) and isinstance(
            command, BeginRecovery
        )
        if policy_begin and admission_state is None:
            raise ValidationError("Care policy begin plans require an admission prestate")
        payload = _recovery_payload(
            selected,
            command,
            rest_admission=rest_admission,
            rest_environment=rest_environment,
            minimum_admission=minimum_admission,
            minimum_environment=minimum_environment,
            bandage_admission=bandage_admission,
            bandage_environment=bandage_environment,
        )

        def assess() -> None:
            assert admission_state is not None and isinstance(command, BeginRecovery)
            admit_environment(self.environment, play, admission_state, command)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            if isinstance(command, BeginRecovery):
                admit_environment(self.environment, play, before, command)
            if rest_admission:
                _require_rest_interval(before, command)
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
            if (
                bandage_admission
                and isinstance(command, FinishRecovery)
                and (task is None or task.kind != "bandage")
            ):
                raise ValidationError(
                    "Captured bandage completion requires its actual bandage task"
                )
            if task is not None:
                context = CareContext(
                    profile_id=task.profile_id,
                    ht=task.ht,
                    skill=task.skill,
                    technology_level=task.technology_level,
                    food=task.food,
                    water=task.water,
                    sleep=task.sleep,
                    physician_skill=task.physician_skill,
                    physician_id=task.physician_id,
                    drug_item_id=task.drug_item_id,
                    drug_hp=task.drug_hp,
                    drug_fp=task.drug_fp,
                    physiology=physiology_traits(
                        _build(play, before, target_id), play.engine.reviewer.compiler.definitions
                    ),
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
            _require_presence(before, command.actor_id, target_id)
            _require_available(before, command.actor_id, kind)
            _require_default_care(play, before, command.actor_id, actor, kind)
            if minimum_admission and kind in {"first-aid", "physician"}:
                skill, _ = care_skill(actor, kind, CareEnvironment())
                assert skill is not None
                minimum.require(before.resources, command.actor_id, skill)
            env = (
                CareEnvironment(
                    task.technology_level, task.food, task.water, task.sleep, task.physician_id
                )
                if task is not None
                else minimum_environment
                or rest_environment
                or bandage_environment
                or self.environment(play, before, target_id)
            )
            if (
                kind == "resuscitate"
                and env.technology_level >= 7
                and any(
                    hazard.active
                    and hazard.actor_id == target_id
                    and hazard.spec.kind == "drowning"
                    and hazard.stage == "rescued"
                    for hazard in before.resources.hazards
                )
            ):
                env = replace(env, resuscitation_first_aid_penalty=2)
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
            context = replace(
                care_context(selected, actor, target, kind, env, physician),
                physiology=physiology_traits(target, play.engine.reviewer.compiler.definitions),
                recover_energy_interval=_recover_energy_interval(
                    play, before, target, target_id, kind, env
                ),
                minimum_skill_admission=minimum_admission,
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

        async def outcome(campaign: Campaign) -> RecoveryResult:
            # A retry reads the persisted result without re-running skills or conditions.
            state = play._load(campaign)
            event = next(e for e in state.resources.events if e.id == f"care:{command.id}")
            return RecoveryResult.model_validate_json(event.kind)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(
                ActsAs(command.actor_id, "Recovery actor does not match authenticated actor"),
            ),
            rng=play.rng,
            assess=assess if policy_begin else None,
        )

    async def execute(
        self, cid: str, command: BeginRecovery | FinishRecovery, *, principal_id: str
    ) -> RecoveryResult:
        command = type(command).model_validate(command)
        play = self.play.for_campaign(await self.play.store.read(cid))
        profile_id = play.engine.reviewer.compiler.statistics_profile
        if profile_id not in ("gurps-lite-4e-2004", "gurps-basic-set-4e-2004"):
            raise ValidationError("Recovery requires an exact GURPS profile")
        ActsAs(command.actor_id, "Recovery actor does not match authenticated actor")(principal_id)
        state = play._load(await play.store.read(cid))
        eligible = profile_id == "gurps-basic-set-4e-2004" and _rest_command(state, command)
        captured = await capture_rest(play, cid, command.id, eligible=eligible)
        minimum_inputs = await capture_minimum(
            play.store,
            cid,
            command.id,
            eligible=profile_id == "gurps-basic-set-4e-2004"
            and isinstance(command, BeginRecovery)
            and command.kind in MINIMUM_KINDS,
            actor_id=command.actor_id,
        )
        bandage_inputs = await capture_bandage(
            play.store,
            state,
            command,
            opted_in=isinstance(self.environment, CarePolicy)
            and self.environment.capture_bandage_environment,
            eligible_profile=profile_id == "gurps-basic-set-4e-2004",
        )
        bandage_environment = None
        if bandage_inputs.enabled and isinstance(command, BeginRecovery):
            if not bandage_inputs.saved:
                require_boundary(await play.store.read(cid), None)
                if state.revision != command.expected_revision:
                    raise ConflictError("Resource revision changed")
            bandage_environment = captured_environment(
                bandage_inputs.environment
                if bandage_inputs.saved
                else asdict(self.environment(play, state, command.target_id))
            )
        minimum_environment = None
        if minimum_inputs.enabled:
            assert isinstance(command, BeginRecovery)
            if not minimum_inputs.saved:
                require_boundary(await play.store.read(cid), None)
                if state.revision != command.expected_revision:
                    raise ConflictError("Resource revision changed")
                actor = _build(play, state, command.actor_id)
                _build(play, state, command.target_id)
                _require_presence(state, command.actor_id, command.target_id)
                _require_available(state, command.actor_id, command.kind)
                _require_default_care(play, state, command.actor_id, actor, command.kind)
                if command.kind in {"first-aid", "physician"}:
                    skill, _ = care_skill(actor, command.kind, CareEnvironment())
                    assert skill is not None
                    minimum.require(state.resources, command.actor_id, skill)
            minimum_environment = _decode_minimum_environment(
                minimum_inputs.environment
                if minimum_inputs.saved
                else asdict(self.environment(play, state, command.target_id))
            )
        environment = None
        if captured.enabled:
            if not captured.saved:
                require_boundary(await play.store.read(cid), None)
                _require_rest_interval(state, command)
            if isinstance(command, BeginRecovery):
                environment = (
                    _decode_rest_environment(captured.environment)
                    if captured.saved
                    else _decode_rest_environment(
                        asdict(self.environment(play, state, command.target_id))
                    )
                )
        return await submit(
            play,
            cid,
            self.plan(
                play,
                cast(ProfileId, profile_id),
                command,
                principal_id=principal_id,
                rest_admission=captured.enabled,
                rest_environment=environment,
                minimum_admission=minimum_inputs.enabled,
                minimum_environment=minimum_environment,
                admission_state=state if isinstance(self.environment, CarePolicy) else None,
                bandage_admission=bandage_inputs.enabled,
                bandage_environment=bandage_environment,
            ),
            principal_id=principal_id,
        )


def _recover_energy_interval(
    play: PlayService,
    state: PlayState,
    target: ValidatedBuild,
    actor_id: str,
    kind: str,
    env: CareEnvironment,
) -> Literal[120, 300] | None:
    if kind != "rest" or env.mana not in RECOVER_ENERGY.mana:
        return None
    expected = next(d for d in healing_package().definitions if d.id == RECOVER_ENERGY.id)
    if play.engine.reviewer.compiler.definitions.get(expected.id) != expected:
        return None
    if not any(p.definition_id == expected.id for p in target.purchases):
        return None
    require_idle_concentration(state.resources, actor_id)
    if any(e.actor_id == actor_id and e.concentrating for e in active_spells(state.resources)):
        raise ValidationError("Recover Energy requires quiet rest without concentration")
    skill = _value(target, expected.id)
    return (
        120
        if skill >= RECOVER_ENERGY.improved_skill
        else 300
        if skill >= RECOVER_ENERGY.minimum_skill
        else None
    )
