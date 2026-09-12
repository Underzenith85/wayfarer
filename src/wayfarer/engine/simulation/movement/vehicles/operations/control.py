"""Control rolls and what losing control does."""

from typing import Literal

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import WaterOccupantCheck
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    internal_id,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    VehicleControl,
)
from wayfarer.engine.simulation.movement.vehicles.motion import (
    control_vehicle,
)
from wayfarer.engine.simulation.movement.vehicles.operations.context import Operation, Outcome
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError


def water_hazard(
    command_id: str,
    state: ResourceState,
    board: HexBattlefield,
    facts: WaterOccupantCheck,
    kind: Literal["drowning", "suffocation", "pressure"],
) -> HazardSchedule:
    """Bind a water casualty to the shared environmental clock."""
    stage: Literal["struggling", "cycles", "exposure"]
    if kind == "drowning":
        interval, cycles, damage, resistible, stage = 5, 100000, 0, True, "struggling"
    elif kind == "suffocation":
        interval, cycles, damage, resistible, stage = 1, 240, 0, False, "cycles"
    elif kind == "pressure":
        interval, cycles, damage, resistible, stage = 1, 100000, 1, True, "exposure"
    else:
        raise ValidationError("Unknown water casualty")
    return HazardSchedule(
        id=internal_id(command_id, kind + ":" + facts.actor_id),
        actor_id=facts.actor_id,
        spec=HazardSpec(
            id="vehicle-" + kind,
            kind=kind,
            scene_id=board.id,
            delay=1 if kind == "suffocation" else 0,
            interval=interval,
            cycles=cycles,
            damage_dice=damage,
            damage_add=0 if damage else 1,
            resistible=resistible,
            reference="Basic Set Campaigns B435-437, B469",
        ),
        started=state.game_time,
        due=state.game_time + interval,
        remaining=cycles,
        ht=facts.ht,
        will=facts.will,
        swimming=facts.swimming,
        stage=stage,
        no_air_since=state.game_time if kind == "suffocation" else None,
        next_check_at=state.game_time + 5 if kind == "drowning" else None,
    )


def resolve(operation: Operation) -> Outcome:
    engine = operation.engine
    state = operation.state
    command = operation.command
    assert isinstance(command, VehicleControl)
    t = operation.transport
    board = operation.board
    rng = operation.rng
    affected: tuple[Transport, ...] = (t,)
    profile_ht = 10
    if t.locomotion != "ground-mount":
        item = next(i for i in state.items if i.id == t.body_id)
        profile = engine.specs[item.definition_id].durability
        assert profile is not None
        profile_ht = profile.ht
    deck = t.open_cabin and t.locomotion == "water"
    if deck and (
        board is None
        or board.profile_id != t.profile_id
        or {f.actor_id for f in command.water_occupants} != set(t.occupants)
    ):
        raise ValidationError("Open-deck control requires compiled checks and a water map")
    if not deck and command.water_occupants:
        raise ValidationError("Open-deck checks only apply to exposed watercraft occupants")
    recovering = t.locomotion == "air" and t.status in ("diving", "stalled")
    if recovering and t.aftermath_turn != state.game_time:
        raise ValidationError("Resolve this turn's air descent before recovery")
    if recovering and t.recovery_turn == state.game_time:
        raise ConflictError("Air recovery already attempted this second")
    controlled = control_vehicle(
        t, command, rng, profile_ht, check_modifiers(state, t.operator_id, "dx")
    )
    if recovering:
        controlled = controlled.model_copy(update={"recovery_turn": state.game_time})
    if deck and controlled.control_margin is not None and controlled.control_margin < 0:
        assert board is not None
        severe = controlled.status in ("capsized", "sinking")
        tossed = tuple(
            facts.actor_id
            for facts in command.water_occupants
            if severe
            or not success_roll(
                t.profile_id,
                facts.hold_skill,
                check_modifiers(state, facts.actor_id, "st"),
                rng=rng,
            ).outcome.succeeded
        )
        if tossed:
            facts_by_actor = {facts.actor_id: facts for facts in command.water_occupants}
            state = state.model_copy(
                update={
                    "hazards": (
                        *state.hazards,
                        *(
                            water_hazard(command.id, state, board, facts_by_actor[a], "drowning")
                            for a in tossed
                        ),
                    )
                }
            )
            controlled = controlled.model_copy(
                update={
                    "occupants": tuple(a for a in controlled.occupants if a not in tossed),
                    "overboard": (*controlled.overboard, *tossed),
                }
            )
    affected = (controlled,)
    return state, affected
