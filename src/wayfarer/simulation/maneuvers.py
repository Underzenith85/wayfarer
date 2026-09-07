"""Persisted maneuver commitments and observable pre-action Wait triggers (B364-366)."""

from typing import Literal

from pydantic import Field

from wayfarer.rules.checks import CheckTrace
from wayfarer.simulation.resources import Id, Record

ATTACK_MANEUVERS = frozenset({"attack", "all_out_attack", "move_and_attack"})
AttackOption = Literal["determined", "strong", "double", "feint"]
DefenseOption = Literal["dodge", "parry", "block", "double"]


class WaitTrigger(Record):
    actor_id: Id
    action: Literal["attack", "move"]
    target_id: Id | None = None
    reaction: Literal["attack", "all_out_attack", "feint", "ready"] = "attack"
    item_id: Id
    reaction_target_id: Id | None = None
    mode_id: str | None = None
    attack_option: AttackOption | None = None


class ManeuverState(Record):
    aim_item_id: str | None = None
    aim_target_id: str | None = None
    aim_seconds: int = Field(default=0, ge=0, le=3)
    aim_accuracy: int = Field(default=0, ge=0)
    aim_mode_id: str | None = None
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
    concentrating: bool = False
    concentration_seconds: int = Field(default=0, ge=0)
    feint_rolls: tuple[CheckTrace, ...] = ()

    @property
    def aim_bonus(self) -> int:
        return self.aim_accuracy + self.aim_seconds - 1 if self.aim_seconds else 0

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


def attack_modifier(state: ManeuverState, target_id: str, target: int) -> int:
    value = target + state.attack_bonus
    if state.evaluate_target_id == target_id:
        value += state.evaluate_bonus
    return min(value, state.attack_cap) if state.attack_cap is not None else value
