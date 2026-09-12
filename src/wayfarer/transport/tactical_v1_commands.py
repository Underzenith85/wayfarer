"""Frozen tactical-v1 input shapes; v2 options belong to the v2 endpoint."""

from typing import Literal

from pydantic import Field

from wayfarer.models import Id, Record
from wayfarer.orchestration.combat import CombatCommand, HexPlacement
from wayfarer.rules.location_types import Hand, HitLocation
from wayfarer.simulation.combat import Defense, Facing, GridPoint, Maneuver, Posture
from wayfarer.simulation.hex_geometry import Cell, Hex, Stairway, _omitted_default
from wayfarer.simulation.maneuvers import DefenseOption
from wayfarer.simulation.unarmed import GrappleLocation, UnarmedAction, UnarmedSkill

AttackOption = Literal["determined", "strong", "double", "feint"]


class TakeUnarmedTurn(CombatCommand):
    kind: Literal["take_unarmed_turn"] = "take_unarmed_turn"
    encounter_id: Id
    action: UnarmedAction
    foot: Literal["left-foot", "right-foot"] = "right-foot"
    target_id: Id
    skill: UnarmedSkill = "attribute:dx"
    hands: tuple[Hand, ...] = ()
    location: GrappleLocation = "torso"
    grip_id: Id | None = None
    enter_close_combat: bool = False


class ChooseDefense(CombatCommand):
    kind: Literal["choose_defense"] = "choose_defense"
    encounter_id: Id
    defense: Defense
    item_id: str | None = None
    second_defense: Defense | None = None
    second_item_id: str | None = None
    retreat: Hex | None = None


# The frozen v1 Wait declaration: a weapon reaction, never an unarmed one.
class WaitTrigger(Record):
    actor_id: Id | None = None
    action: Literal["attack", "move"]
    target_id: Id | None = None
    reaction: Literal["attack", "all_out_attack", "feint", "ready"] = "attack"
    item_id: Id
    reaction_target_id: Id | None = None
    mode_id: str | None = None
    attack_option: AttackOption | None = None
    zone: tuple[tuple[int, int], ...] = ()
    stop_thrust: bool = False


class TakeCombatTurn(CombatCommand):
    kind: Literal["take_combat_turn"] = "take_combat_turn"
    encounter_id: Id
    maneuver: Maneuver
    destination: GridPoint | None = None
    facing: Facing | None = None
    posture: Posture | None = None
    item_id: str | None = None
    target_id: str | None = None
    mode_id: str | None = None
    shots: int = Field(default=1, ge=1, le=100)
    reload_ammunition_id: str | None = None
    unload_ammunition: bool = Field(default=False, exclude_if=lambda v: not v)
    hit_location: HitLocation | None = None
    target_item_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    ready_hand: Hand | Literal["both"] | None = None
    attack_option: AttackOption | None = None
    defense_option: DefenseOption | None = None
    wait_trigger: WaitTrigger | None = None
    step_timing: Literal["before", "after"] = "before"
    second_item_id: str | None = None
    second_target_id: str | None = None
    second_mode_id: str | None = None
    braced: bool = Field(default=False, exclude_if=lambda value: not value)
    hex_path: tuple[Hex, ...] = Field(default=(), max_length=100)
    hex_facing: Literal[0, 1, 2, 3, 4, 5] | None = None


class HexBattlefield(Record):
    """New tagged contract. Legacy square maps cannot validate as hex maps."""

    id: Id
    coordinate_system: Literal["hex-axial-v1"]
    profile_id: Literal["gurps-basic-set-4e-2004"]
    baseline_id: Literal["gurps-4e-characters-3p-2008+campaigns-4p-2008"]
    cells: tuple[Cell, ...] = Field(min_length=1, max_length=10000)
    stairs: tuple[Stairway, ...] = Field(
        default=(), exclude_if=lambda v: not v, json_schema_extra=_omitted_default
    )


class MigrateEncounterHex(CombatCommand):
    kind: Literal["migrate_encounter_hex"] = "migrate_encounter_hex"
    encounter_id: Id
    battlefield: HexBattlefield
    placements: tuple[HexPlacement, ...] = Field(min_length=2, max_length=100)
