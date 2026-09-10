"""Tactical v2 equipment status and pure previews of timed work."""

import hashlib

from wayfarer.errors import WayfarerError
from wayfarer.orchestration.combat import RepairEquipment, RetrieveEquipment, TakeCombatTurn
from wayfarer.orchestration.equipment_retrieval import RetrievalTask
from wayfarer.orchestration.equipment_retrieval import tasks as retrievals
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import TacticalSnapshot
from wayfarer.rules.object_types import GroundPosition, ObjectCondition
from wayfarer.rules.readiness_types import ProjectileProgress
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.object_repairs import RepairTask
from wayfarer.simulation.object_repairs import tasks as repairs
from wayfarer.simulation.resources import Record


class EquipmentChoice(Record):
    label: str
    command: RepairEquipment | RetrieveEquipment | TakeCombatTurn


class EquipmentView(Record):
    id: str
    name: str
    condition: ObjectCondition | None
    ground: GroundPosition | None
    work: str | None = None
    due_in: int | None = None
    choices: tuple[EquipmentChoice, ...] = ()
    readiness: ProjectileProgress | None = None
    loaded_rounds: int | None = None


class TacticalSnapshotV2(TacticalSnapshot):
    version: str = "tactical-v2"
    equipment: tuple[EquipmentView, ...] = ()


def equipment_view(play: PlayService, state: PlayState, actor_id: str) -> tuple[EquipmentView, ...]:
    encounter = next((e for e in reversed(state.encounters) if actor_id in e.turn_order), None)
    result: list[EquipmentView] = []
    work_tasks: tuple[RepairTask | RetrievalTask, ...] = (
        *repairs(state.resources),
        *retrievals(state.resources),
    )
    for item in state.resources.items:
        load = next((v for v in state.resources.ammunition_loads if v.weapon_id == item.id), None)
        progress = load.readiness if load else None
        if item.owner_id != actor_id or (
            item.condition is None and item.ground is None and progress is None
        ):
            continue
        work = next(
            (
                t
                for t in work_tasks
                if t.item_id == item.id and t.actor_id == actor_id and t.status == "pending"
            ),
            None,
        )
        choices: list[EquipmentChoice] = []
        if encounter is not None and state.lifecycle == "active":
            for stage in ("finish", "cancel") if work else ("start",):
                retrieving = item.ground is not None
                model = RetrieveEquipment if retrieving else RepairEquipment
                kind = "retrieve_equipment" if retrieving else "repair_equipment"
                identifier = (
                    "equipment:"
                    + hashlib.sha256(
                        f"{state.campaign_id}:{state.revision}:{item.id}:{kind}:{stage}".encode()
                    ).hexdigest()
                )
                command = model.model_validate(
                    dict(
                        id=identifier,
                        actor_id=actor_id,
                        expected_revision=state.revision,
                        encounter_id=item.ground.encounter_id if item.ground else encounter.id,
                        item_id=item.id,
                        stage=stage,
                        task_id=work.id if work else None,
                    )
                )
                try:
                    from wayfarer.orchestration.recovery import guard

                    guard(state, actor_id, kind)
                    if isinstance(command, RetrieveEquipment):
                        from wayfarer.orchestration.equipment_retrieval import retrieve

                        original = next(e for e in state.encounters if e.id == command.encounter_id)
                        retrieve(
                            play,
                            state,
                            original,
                            actor_id=actor_id,
                            item_id=item.id,
                            command_id=identifier,
                            stage=command.stage,
                            task_id=command.task_id,
                        )
                    else:
                        from wayfarer.orchestration.object_repairs import repair

                        repair(
                            play,
                            state,
                            actor_id=actor_id,
                            item_id=item.id,
                            command_id=identifier,
                            stage=command.stage,
                            task_id=command.task_id,
                            preview=True,
                        )
                    choices.append(
                        EquipmentChoice(
                            label=f"{stage.title()} {'retrieval' if retrieving else 'repair'}",
                            command=command,
                        )
                    )
                except WayfarerError, ValueError:
                    continue
        result.append(
            EquipmentView(
                id=item.id,
                name=item.definition_id,
                condition=item.condition,
                ground=item.ground,
                work=("Retrieval" if item.ground else "Repair") if work else None,
                due_in=max(0, work.due - state.resources.game_time) if work else None,
                choices=tuple(choices),
                readiness=progress,
                loaded_rounds=load.rounds if load else None,
            )
        )
    if encounter is not None:
        from wayfarer.orchestration.thrown_items import record, recover
        from wayfarer.orchestration.unarmed import free_hands

        for item in state.resources.expended_items:
            landing = record(state.resources, item.id)
            if (
                landing is None
                or landing.encounter_id != encounter.id
                or actor_id not in landing.observed_by
            ):
                continue
            options: list[EquipmentChoice] = []
            if encounter.current_actor_id == actor_id and encounter.status == "active":
                for hand in free_hands(state, encounter, actor_id):
                    command_id = (
                        "recover:"
                        + hashlib.sha256(
                            f"{state.campaign_id}:{state.revision}:{item.id}:{actor_id}:{hand}".encode()
                        ).hexdigest()
                    )
                    recovery = TakeCombatTurn(
                        id=command_id,
                        actor_id=actor_id,
                        expected_revision=state.revision,
                        encounter_id=encounter.id,
                        maneuver="ready",
                        item_id=item.id,
                        ready_hand=hand,
                        recover_thrown_item=True,
                    )
                    try:
                        recover(play, state, encounter, recovery)
                    except WayfarerError, ValueError:
                        continue
                    options.append(EquipmentChoice(label=f"Recover with {hand}", command=recovery))
            result.append(
                EquipmentView(
                    id=item.id,
                    name=item.definition_id,
                    condition=item.condition if item.owner_id == actor_id else None,
                    ground=landing.landing,
                    work="Thrown item" if landing.landing else "Landing unresolved",
                    choices=tuple(options),
                )
            )
    return tuple(result)
