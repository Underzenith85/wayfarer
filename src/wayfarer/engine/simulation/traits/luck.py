"""B66 Luck: atomic dice replacement and elapsed real-play-time cooldowns.

The host records the currently pending roll before invoking this reducer and
persists the returned snapshot under its revision lock. ``real_time`` is trusted
elapsed seconds of play, never campaign time or a player-supplied clock. Secret
rolls must be declared before rolling; their records stay in GM-only storage.
Source limitations are checked against the trusted pending roll before spending a use.
"""

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.resources import Command
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

LUCK_ID = "trait:advantage:luck"
COOLDOWNS = {15: 3600, 30: 1800, 60: 600}
Die = Annotated[int, Field(ge=1, le=6)]


class LuckRoll(Record):
    """Trusted unresolved roll; each original face is retained for audit."""

    id: str
    actor_id: str
    kind: Literal["success", "damage", "reaction"] = "success"
    dice_count: int = Field(default=3, ge=1, le=100)
    modifier: int = 0
    original: tuple[Die, ...] | None = None
    scope: Literal["own", "party-event", "attack"] = "own"
    affected_actor_ids: tuple[str, ...] = ()
    secret: bool = False
    task_class: Literal[
        "other",
        "athletics",
        "social",
        "job",
        "weapon",
        "active-defense",
        "close-combat-st-dx",
        "resistance",
        "injury-ht",
        "opponent-critical",
    ] = "other"
    failed: bool = False
    chosen_dice: tuple[Die, ...] | None = None
    chosen_total: int | None = None

    @model_validator(mode="after")
    def valid_dice(self) -> LuckRoll:
        if self.kind in {"success", "reaction"} and self.dice_count != 3:
            raise ValueError("Success and reaction rolls require 3d6")
        if self.original is not None and len(self.original) != self.dice_count:
            raise ValueError("Original dice do not match the authored expression")
        return self


class LuckCommand(Command):
    roll_id: str


class LuckReceipt(Record):
    command: LuckCommand
    real_time: int = Field(ge=0)
    available_at: int = Field(ge=0)
    attempts: tuple[tuple[Die, ...], ...]
    chosen_index: int = Field(ge=0, le=2)
    secret: bool


class LuckState(Record):
    revision: int = Field(default=0, ge=0)
    game_time: int = Field(default=0, ge=0)
    real_time: int = Field(default=0, ge=0)
    rolls: tuple[LuckRoll, ...] = ()
    pending_roll_id: str | None = None
    receipts: tuple[LuckReceipt, ...] = ()

    @model_validator(mode="after")
    def unique_records(self) -> LuckState:
        if len({roll.id for roll in self.rolls}) != len(self.rolls):
            raise ValueError("Duplicate Luck roll IDs")
        if len({use.command.id for use in self.receipts}) != len(self.receipts):
            raise ValueError("Duplicate Luck command IDs")
        return self


def luck_cooldown(build: ValidatedBuild, definitions: Mapping[str, RuleDefinition]) -> int:
    """Join the canonical approved purchase to the three B66 tiers."""
    effects = [
        effect
        for effect in mundane_trait_effects(build, definitions)
        if effect.definition_id == LUCK_ID
    ]
    purchases = [
        purchase for purchase in build.trait_purchases if purchase.definition_id == LUCK_ID
    ]
    if len(effects) != 1 or len(purchases) != 1:
        raise ValidationError("Luck is unavailable in the approved build")
    purchase = purchases[0]
    if purchase.trait is None:
        raise ValidationError("Luck construction is missing")
    try:
        return COOLDOWNS[effects[0].point_cost]
    except KeyError as exc:
        raise ValidationError("Unknown Luck tier") from exc


def _check_limitations(build: ValidatedBuild, roll: LuckRoll) -> None:
    purchase = next(p for p in build.trait_purchases if p.definition_id == LUCK_ID)
    assert purchase.trait is not None
    modifiers = set(purchase.trait.modifiers)
    if "active" in modifiers and roll.original is not None:
        raise ValidationError("Active Luck must be declared before dice are rolled")
    aspects = {
        "aspected-athletics": {"athletics"},
        "aspected-social": {"social"},
        "aspected-job": {"job"},
        "aspected-combat": {"weapon", "active-defense", "close-combat-st-dx"},
    }
    for modifier, allowed in aspects.items():
        if modifier in modifiers and roll.task_class not in allowed:
            raise ValidationError("Pending roll is outside the purchased Luck aspect")
    if "defensive" in modifiers:
        eligible = roll.failed and roll.task_class in {"active-defense", "resistance", "injury-ht"}
        eligible |= roll.scope == "attack" and roll.task_class == "opponent-critical"
        if not eligible:
            raise ValidationError(
                "Defensive Luck requires a failed defense or an incoming critical hit"
            )


def apply_luck(
    state: LuckState,
    command: LuckCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    *,
    real_time: int,
    seed: str,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[LuckState, LuckReceipt]:
    """Resolve one immediate Luck choice and consume its elapsed-play cooldown.

    Equal totals keep the earlier attempt. The seed comes from persisted command
    entropy, so a transaction retry produces identical dice. Exact command replay
    returns its original receipt without drawing dice or consuming another use.
    """
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Luck requires trusted actor authority")
    prior = next((use for use in state.receipts if use.command.id == command.id), None)
    if prior is not None:
        if prior.command != command:
            raise ConflictError("Luck command ID was already used")
        return state, prior
    if state.revision != command.expected_revision:
        raise ConflictError("Luck revision changed")
    if type(real_time) is not int or real_time < state.real_time:
        raise ValidationError("Elapsed real-play time cannot go backwards")
    cooldown = luck_cooldown(build, definitions)
    roll = next((value for value in state.rolls if value.id == command.roll_id), None)
    if roll is None or state.pending_roll_id != roll.id or roll.chosen_dice is not None:
        raise ValidationError("Luck must be declared immediately for a pending roll")
    _check_limitations(build, roll)
    if roll.scope == "own":
        eligible = roll.actor_id == command.actor_id
    else:
        eligible = command.actor_id in roll.affected_actor_ids
    if not eligible:
        raise ValidationError("Luck cannot be shared with another actor")
    if roll.secret and roll.original is not None:
        raise ValidationError("Secret Luck must be declared before the GM rolls")
    if (
        roll.original is None
        and not roll.secret
        and roll.scope != "attack"
        and not any(
            p.trait is not None and "active" in p.trait.modifiers
            for p in build.trait_purchases
            if p.definition_id == LUCK_ID
        )
    ):
        raise ValidationError("Ordinary Luck requires the initial roll")
    previous_uses = [use for use in state.receipts if use.command.actor_id == command.actor_id]
    if previous_uses and real_time < max(use.available_at for use in previous_uses):
        raise ValidationError("Luck is cooling down in real play time")
    rng = SeededRandom(seed)
    attempts: tuple[tuple[int, ...], ...] = () if roll.original is None else (roll.original,)
    attempts += tuple(
        tuple(1 + rng.randbelow(6) for _ in range(roll.dice_count))
        for _ in range(3 - len(attempts))
    )
    # Own success wants low, own damage/reaction want high. Against an attacker
    # choose their worst: high success-roll total, low damage total.
    lower_is_better = roll.kind == "success"
    if roll.scope == "attack":
        if roll.kind == "reaction":
            raise ValidationError("An attack cannot be a reaction roll")
        lower_is_better = not lower_is_better
    totals = tuple(sum(attempt) + roll.modifier for attempt in attempts)
    choose = min if lower_is_better else max
    chosen_index = choose(range(3), key=lambda index: totals[index])
    updated = roll.model_copy(
        update={"chosen_dice": attempts[chosen_index], "chosen_total": totals[chosen_index]}
    )
    receipt = LuckReceipt(
        command=command,
        real_time=real_time,
        available_at=real_time + cooldown,
        attempts=attempts,
        chosen_index=chosen_index,
        secret=roll.secret,
    )
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "real_time": real_time,
            "rolls": tuple(updated if value.id == roll.id else value for value in state.rolls),
            "pending_roll_id": None,
            "receipts": state.receipts + (receipt,),
        }
    ), receipt


def visible_history(
    state: LuckState, *, viewer_actor_id: str, gm: bool = False
) -> tuple[LuckReceipt, ...]:
    """Secret GM rolls reveal no dice to players; other uses stay actor-local."""
    return tuple(
        use
        for use in state.receipts
        if gm or (not use.secret and use.command.actor_id == viewer_actor_id)
    )
