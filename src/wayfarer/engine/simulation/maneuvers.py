"""Persisted maneuver commitments and observable pre-action Wait triggers (B364-366)."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.unarmed import UnarmedReaction
from wayfarer.models import Id, Record

ATTACK_MANEUVERS = frozenset({"attack", "all_out_attack", "move_and_attack"})
AttackOption = Literal["determined", "strong", "double", "feint", "suppression"]
DefenseOption = Literal["dodge", "parry", "block", "double"]


class WaitTrigger(Record):
    actor_id: Id | None = None
    action: Literal["attack", "move"]
    target_id: Id | None = None
    reaction: Literal["attack", "all_out_attack", "feint", "ready"] = "attack"
    item_id: Id | None = None
    reaction_target_id: Id | None = None
    mode_id: str | None = None
    attack_option: AttackOption | None = None
    zone: tuple[tuple[int, int], ...] = ()
    stop_thrust: bool = False
    # Omitted unless declared, so an armed Wait keeps its exact canonical payload.
    unarmed: UnarmedReaction | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def valid_condition(self) -> WaitTrigger:
        if (self.item_id is None) == (self.unarmed is None):
            raise ValueError("A Wait reaction declares either a weapon or an unarmed attack")
        if self.unarmed is not None and (
            self.reaction not in ("attack", "all_out_attack")
            or self.mode_id is not None
            or self.stop_thrust
        ):
            raise ValueError("An unarmed Wait reaction is an ordinary or All-Out Attack")
        if self.attack_option == "suppression":
            raise ValueError("Suppression fire requires an immediate mapped declaration")
        if len(set(self.zone)) != len(self.zone):
            raise ValueError("Wait zone contains duplicate hexes")
        if self.zone and self.action != "move":
            raise ValueError("A Wait zone triggers on movement")
        if self.stop_thrust and (
            self.action != "attack" or self.reaction not in ("attack", "all_out_attack")
        ):
            raise ValueError("Stop thrust requires an attack reaction to an attack")
        return self


class ManeuverState(Record):
    aim_item_id: str | None = None
    aim_target_id: str | None = None
    aim_seconds: int = Field(default=0, ge=0, le=3)
    aim_accuracy: int = Field(default=0, ge=0)
    aim_mode_id: str | None = None
    aim_braced: bool = False
    aim_sight_bonus: int = Field(default=0, ge=0)
    evaluate_target_id: str | None = None
    evaluate_bonus: int = Field(default=0, ge=0, le=3)
    attack_bonus: int = 0
    attack_cap: int | None = None
    strong: bool = False
    defense_forbidden: bool = False
    parry_forbidden: bool = False
    enhanced_defense: DefenseOption | None = None
    feint_target_id: str | None = None
    feint_penalty: int = Field(default=0, ge=0)
    wait: WaitTrigger | None = None
    defended: bool = False
    attacks_remaining: int = Field(default=0, ge=0, le=1)
    second_attack_item_id: str | None = None
    second_attack_target_id: str | None = None
    second_attack_mode_id: str | None = None
    second_attack_penalty: int = Field(default=0, ge=-4, le=0)
    stop_thrust_damage_bonus: int = Field(default=0, ge=0)
    concentrating: bool = False
    concentration_seconds: int = Field(default=0, ge=0)
    feint_rolls: tuple[CheckTrace, ...] = ()

    @property
    def aim_bonus(self) -> int:
        return (
            self.aim_accuracy + self.aim_seconds - 1 + int(self.aim_braced) + self.aim_sight_bonus
            if self.aim_seconds
            else 0
        )

    def new_turn(self) -> ManeuverState:
        # Aim/Evaluate/Feint survive until the next maneuver is selected, not
        # merely until that turn starts. Wait alone expires at turn start.
        return self.model_copy(update={"wait": None})


class WaitInterrupt(Record):
    waiter_id: Id
    actor_id: Id
    turn_index: int = Field(ge=0)
    command_json: str
    declaration: WaitTrigger
    reacting: bool = False
    ready: bool = False


def attack_modifier(
    state: ManeuverState, target_id: str, target: int, *, check_adjustment: int = 0
) -> int:
    # A maneuver caps the final modified skill. Return a base target so the
    # caller can still record its condition modifiers explicitly in the trace.
    value = target + state.attack_bonus + check_adjustment
    if state.evaluate_target_id == target_id:
        value += state.evaluate_bonus
    return (
        min(value, state.attack_cap) if state.attack_cap is not None else value
    ) - check_adjustment
