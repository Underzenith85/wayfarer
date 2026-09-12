"""Tactical v2 equipment status and pure previews of timed work."""

import hashlib
from typing import Literal

from wayfarer.engine.rules.types.object import GroundPosition, ObjectCondition
from wayfarer.engine.rules.types.readiness import ProjectileProgress
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.equipment.repairs import RepairTask
from wayfarer.engine.simulation.equipment.repairs import tasks as repairs
from wayfarer.engine.simulation.equipment.retrieval import RetrievalTask
from wayfarer.engine.simulation.equipment.retrieval import tasks as retrievals
from wayfarer.errors import WayfarerError
from wayfarer.models import Record
from wayfarer.orchestration.combat import (
    MigrateEncounterBasic,
    RepairEquipment,
    RetrieveEquipment,
    TakeCombatTurn,
    WithdrawEncounter,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import TacticalChoice, TacticalSnapshot


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
    charges: int | None = None


class TacticalMigrationChoice(Record):
    label: str
    command: MigrateEncounterBasic


class TacticalWithdrawalChoice(Record):
    label: str
    command: WithdrawEncounter


class BasicTacticalActor(Record):
    id: str
    name: str
    controlled: bool
    posture: str
    grappled: bool
    pinned: bool


class BasicTacticalEncounter(Record):
    id: str
    status: str
    round: int
    current_actor_id: str | None
    actors: tuple[BasicTacticalActor, ...]
    choices: tuple[TacticalChoice, ...]
    notice: str | None = None


class TacticalActivity(Record):
    kind: Literal["combat", "waiting", "independent"]
    representation: Literal["basic", "square", "hex"] | None = None
    message: str
    ready_through: int
    paused: bool


class TacticalSnapshotV2(TacticalSnapshot):
    version: str = "tactical-v2"
    equipment: tuple[EquipmentView, ...] = ()
    migrations: tuple[TacticalMigrationChoice, ...] = ()
    withdrawals: tuple[TacticalWithdrawalChoice, ...] = ()
    basic_encounters: tuple[BasicTacticalEncounter, ...] = ()
    activity: TacticalActivity


def equipment_view(play: PlayService, state: PlayState, actor_id: str) -> tuple[EquipmentView, ...]:
    encounter = next((e for e in reversed(state.encounters) if actor_id in e.turn_order), None)
    from wayfarer.engine.simulation.combat.explosions import blasts

    pending_blasts = tuple(b for b in blasts(state.resources) if not b.resolved)
    result: list[EquipmentView] = []
    work_tasks: tuple[RepairTask | RetrievalTask, ...] = (
        *repairs(state.resources),
        *retrievals(state.resources),
    )
    for item in state.resources.items:
        load = next((v for v in state.resources.ammunition_loads if v.weapon_id == item.id), None)
        progress = load.readiness if load else None
        blast = next((b for b in pending_blasts if b.source_item_id == item.id), None)
        if item.owner_id != actor_id or (
            item.condition is None
            and item.ground is None
            and progress is None
            and item.charges is None
            and item.firearm_failure is None
            and blast is None
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
                        from wayfarer.engine.simulation.equipment.retrieval import (
                            retrieve,
                        )

                        original = next(e for e in state.encounters if e.id == command.encounter_id)
                        retrieve(
                            play.rules_context,
                            state,
                            original,
                            actor_id=actor_id,
                            item_id=item.id,
                            command_id=identifier,
                            stage=command.stage,
                            task_id=command.task_id,
                        )
                    else:
                        from wayfarer.engine.simulation.equipment.repair_transitions import repair

                        repair(
                            play.rules_context,
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
                work="Explosion pending"
                if blast
                else ("Retrieval" if item.ground else "Repair")
                if work
                else item.firearm_failure.kind
                if item.firearm_failure
                else None,
                due_in=max(0, blast.due - state.resources.game_time)
                if blast
                else max(0, work.due - state.resources.game_time)
                if work
                else None,
                choices=tuple(choices),
                readiness=progress,
                loaded_rounds=load.rounds if load else None,
                charges=item.charges,
            )
        )
    if encounter is not None:
        from wayfarer.engine.simulation.combat.thrown.items import record, recover
        from wayfarer.engine.simulation.combat.unarmed.fighters import free_hands

        for item in state.resources.expended_items:
            landing = record(state.resources, item.id)
            if (
                landing is None
                or landing.encounter_id != encounter.id
                or actor_id not in landing.observed_by
            ):
                continue
            blast = next((b for b in pending_blasts if b.source_item_id == item.id), None)
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
                        recover(play.rules_context, state, encounter, recovery)
                    except WayfarerError, ValueError:
                        continue
                    options.append(EquipmentChoice(label=f"Recover with {hand}", command=recovery))
            result.append(
                EquipmentView(
                    id=item.id,
                    name=item.definition_id,
                    condition=item.condition if item.owner_id == actor_id else None,
                    ground=landing.landing,
                    work="Explosion pending"
                    if blast
                    else item.firearm_failure.kind
                    if item.firearm_failure
                    else "Thrown item"
                    if landing.landing
                    else "Landing unresolved",
                    due_in=max(0, blast.due - state.resources.game_time) if blast else None,
                    choices=tuple(options),
                )
            )
    return tuple(result)
