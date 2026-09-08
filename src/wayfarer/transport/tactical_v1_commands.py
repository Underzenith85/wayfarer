"""Frozen tactical-v1 input shapes; v2 options belong to the v2 endpoint."""

from typing import Literal

from wayfarer.orchestration.combat import CombatCommand
from wayfarer.rules.location_types import Hand
from wayfarer.simulation.combat import Defense
from wayfarer.simulation.hex_geometry import Hex
from wayfarer.simulation.resources import Id
from wayfarer.simulation.unarmed import GrappleLocation, UnarmedAction, UnarmedSkill


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
