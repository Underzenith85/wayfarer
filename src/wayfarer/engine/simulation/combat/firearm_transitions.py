"""Conventional TL5+ firearm malfunctions and Ready servicing (B382/B407)."""

from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RecordedDice, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.firearm import FirearmFailure
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.firearms import spend_rounds
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def set_failure(
    state: ResourceState, item_id: str, failure: FirearmFailure | None
) -> ResourceState:
    return state.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"firearm_failure": failure}) if i.id == item_id else i
                for i in state.items
            )
        }
    )


def validate_attack(state: ResourceState, item_id: str, weapon: RangedMode, shots: int) -> None:
    item = next(i for i in state.items if i.id == item_id)
    failure = item.firearm_failure
    if failure is None:
        return
    if (
        weapon.firearm is None
        or weapon.firearm.action != "revolver"
        or failure.kind != "misfire"
        or failure.mode_id != weapon.id
    ):
        raise ValidationError("Firearm failure must be serviced before firing")
    load = next((v for v in state.ammunition_loads if v.weapon_id == item_id), None)
    if load is None or load.rounds < shots + int(failure.blocked_round):
        raise ValidationError("Revolver needs usable ammunition beyond its misfired round")


def before_attack(state: ResourceState, item_id: str, weapon: RangedMode) -> ResourceState:
    item = next(i for i in state.items if i.id == item_id)
    failure = item.firearm_failure
    if failure is not None and weapon.firearm is not None:
        assert failure.kind == "misfire" and weapon.firearm.action == "revolver"
        # Advancing the cylinder retires the unusable round, not a fired shot.
        state = spend_rounds(state, item_id, int(failure.blocked_round))
        state = set_failure(state, item_id, None)
    return state


def roll_malfunction(
    runtime: RulesContext,
    weapon: RangedMode,
    attack: CheckTrace,
    *,
    cause_id: str,
    shots: int,
    rapid_bonus: int,
) -> tuple[CheckTrace, int, tuple[int, ...], FirearmFailure | None]:
    spec = weapon.firearm
    if spec is None or attack.total < spec.malfunction_number:
        return attack, shots, (), None
    table = draw_dice(runtime.rng, 3)
    total = sum(table)
    kind: Literal["mechanical", "stoppage", "misfire", "explosion", "delayed", "dud"] = (
        "mechanical" if total <= 4 or total >= 15 else "stoppage" if 9 <= total <= 11 else "misfire"
    )
    if total >= 15 and (
        spec.technology_level == 3
        or spec.technology_level == 4
        and spec.action in ("grenade", "breechloader", "repeating")
    ):
        kind = "explosion"
    elif spec.action == "grenade":
        kind = "delayed" if kind == "mechanical" else "dud"
    elif spec.action == "beam" and kind == "stoppage":
        kind = "mechanical"
    elif spec.action == "single-use" and kind == "stoppage":
        kind = "dud"
    failure = FirearmFailure(
        mode_id=weapon.id,
        cause_id=cause_id,
        kind=kind,
        blocked_round=kind == "misfire" and spec.action != "beam",
        diagnosed=kind == "stoppage",
    )
    fired = int(kind == "stoppage")
    if fired:
        # The single shot uses the already-recorded attack dice, without the burst bonus.
        single = success_roll(
            "gurps-basic-set-4e-2004",
            attack.base_target - rapid_bonus,
            attack.modifiers,
            rng=RecordedDice(list(attack.dice)),
        )
        attack = replace(single, rule_id="gurps.combat.ranged_attack")
    # B382 gives malfunction priority: no second critical-miss table/consequences.
    attack = replace(
        attack, outcome=Outcome.SUCCESS if fired and attack.outcome.succeeded else Outcome.FAILURE
    )
    return attack, fired, table, failure


def service(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn,
    *,
    validate_only: bool = False,
) -> ResourceState:
    from wayfarer.engine.rules.types.skill import ControllingAttribute
    from wayfarer.engine.simulation.actors import build, catalog, level
    from wayfarer.engine.simulation.combat.objects.locations import unavailable_hand
    from wayfarer.engine.simulation.health.hit_locations import disabled

    equipment = catalog(runtime)
    if equipment.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Firearm service requires the exact Basic Set profile")
    if (
        command.maneuver != "ready"
        or command.reload_ammunition_id is not None
        or command.unload_ammunition
    ):
        raise ValidationError("Firearm service requires a separate Ready maneuver")
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id or not item.equipped:
        raise ValidationError("Firearm service requires an owned equipped weapon")
    failure = item.firearm_failure
    if failure is None or failure.kind in ("destroyed", "dud", "delayed", "explosion"):
        raise ValidationError("Firearm has no serviceable failure")
    if command.mode_id not in (None, failure.mode_id):
        raise ValidationError("Firearm service must match the failed mode")
    entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
    weapon = next(m for m in entry.modes if m.id == failure.mode_id)
    if not isinstance(weapon, RangedMode) or weapon.firearm is None:
        raise ValidationError("Firearm service requires explicit firearm metadata")
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    unavailable = disabled(state.resources, command.actor_id)
    if command.firearm_service != "diagnose" and (
        any(unavailable_hand(unavailable, h) for h in ("left-hand", "right-hand"))
        or any(
            i.owner_id == command.actor_id
            and i.id != item.id
            and i.equipped
            and i.ready
            and (
                next(e for e in equipment.entries if e.definition_id == i.definition_id).modes
                or next(e for e in equipment.entries if e.definition_id == i.definition_id).shield
            )
            for i in state.resources.items
        )
    ):
        raise ValidationError("Firearm service requires two available hands")
    if actor.grappled or any(
        g.holder_id == actor.actor_id or g.target_id == actor.actor_id for g in encounter.grips
    ):
        raise ValidationError("Firearm service is unavailable while grappled")
    operation = command.firearm_service
    if operation == "clear" and (failure.kind == "mechanical" or not failure.diagnosed):
        raise ValidationError("Diagnose the failure and use the appropriate firearm repair")
    if operation == "repair" and (failure.kind != "mechanical" or not failure.diagnosed):
        raise ValidationError("Repair requires a diagnosed mechanical failure")
    if operation == "repair" and command.firearm_service_skill != "armoury":
        raise ValidationError("Mechanical repair requires Armoury")
    compiled = build(runtime, state, command.actor_id)
    stats = compiled.statistics
    assert stats is not None
    if command.firearm_service_skill == "armoury":
        skill_id = weapon.firearm.armoury_skill_id
        if skill_id is None:
            raise ValidationError("No pinned Armoury skill for this firearm")
        target = int(level(compiled, skill_id).value)
    else:
        definition = runtime.reviewer.compiler.definitions[weapon.skill_id]
        if definition.skill is None or definition.skill.attribute != ControllingAttribute.DX:
            raise ValidationError("IQ-based firearm service requires a DX-based weapon skill")
        target = int(level(compiled, weapon.skill_id).value) - stats.dx + stats.iq
    if weapon.smartgun is not None:
        target += weapon.smartgun.service_bonus
    if failure.kind == "misfire" and command.firearm_service_skill == "armoury":
        target += 2
    elif (
        operation == "clear"
        and failure.kind == "stoppage"
        and command.firearm_service_skill == "weapon"
    ):
        target -= 4
    if validate_only:
        return state.resources
    progress = (
        failure.progress
        if (failure.service_actor_id, failure.service_kind, failure.service_skill)
        == (command.actor_id, operation, command.firearm_service_skill)
        else 0
    )
    progress += 1
    duration = 3600 if operation == "repair" else 3 if operation == "clear" else 1
    resources = state.resources
    if progress < duration:
        return set_failure(
            resources,
            item.id,
            failure.model_copy(
                update={
                    "progress": progress,
                    "service_actor_id": command.actor_id,
                    "service_kind": operation,
                    "service_skill": command.firearm_service_skill,
                }
            ),
        )
    roll = success_roll(
        equipment.profile_id,
        target,
        check_modifiers(state.resources, command.actor_id, "iq"),
        rng=runtime.rng,
    )
    updated: FirearmFailure | None = failure.model_copy(update={"progress": 0})
    if roll.outcome.succeeded:
        if operation == "diagnose":
            updated = failure.model_copy(update={"diagnosed": True, "progress": 0})
        else:
            resources = spend_rounds(resources, item.id, int(failure.blocked_round))
            updated = None
    elif roll.outcome is Outcome.CRITICAL_FAILURE and operation != "diagnose":
        updated = failure.model_copy(
            update={
                "kind": "destroyed" if operation == "repair" else "mechanical",
                "diagnosed": False,
                "progress": 0,
            }
        )
    resources = set_failure(resources, item.id, updated)
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=f"firearm-service:{command.id}",
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=ServiceRecord(
                        command_id=command.id,
                        weapon_id=item.id,
                        operation=operation or "diagnose",
                        skill=command.firearm_service_skill,
                        roll=roll,
                        before=failure,
                        after=updated,
                    ).model_dump_json(),
                ),
            )
        }
    )


class ServiceRecord(Record):
    command_id: str
    weapon_id: str
    operation: str
    skill: str
    roll: CheckTrace
    before: FirearmFailure
    after: FirearmFailure | None
