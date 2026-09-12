"""Typed numeric fright consequences (Basic Set Campaigns B360-361).

GM-assigned traits and panic choices remain explicit decisions; they are never
invented by the model or treated as automatic character purchases.
"""

from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace, Modifier, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class FrightEffect(Record):
    table_total: int = Field(ge=4)
    condition: Literal[
        "none", "stunned", "retching", "unconscious", "panic", "catatonia", "seizure"
    ] = "none"
    duration_seconds: int = Field(default=0, ge=0)
    recovery_attribute: Literal["none", "ht", "will", "modified-will"] = "none"
    recovery_interval_seconds: int = Field(default=0, ge=0)
    repeat_duration_dice: int = Field(default=0, ge=0)
    repeat_duration_unit: int = Field(default=1, ge=1)
    hp_loss: int = Field(default=0, ge=0)
    fp_loss: int = Field(default=0, ge=0)
    permanent_ht_loss: int = Field(default=0, ge=0)
    permanent_iq_loss: int = Field(default=0, ge=0)
    trait_choice: Literal[
        "none", "quirk", "delusion", "mental", "physical", "worsen-self-control"
    ] = "none"
    trait_points: int = Field(default=0, le=0)
    aftermath_penalty: int = 0
    aftermath_seconds: int = Field(default=0, ge=0)
    neglect_progression: bool = False
    panic_severity: int | None = None
    dice: tuple[int, ...] = ()
    checks: tuple[CheckTrace, ...] = ()


def fright_effect(
    total: int, ht: int, *, rng: RandomSource, check_modifiers: tuple[Modifier, ...] = ()
) -> FrightEffect:
    if type(total) is not int or total < 4 or type(ht) is not int or ht < 1:
        raise ValidationError("Invalid fright consequence inputs")
    dice: list[int] = []
    checks: list[CheckTrace] = []

    def roll(count: int) -> int:
        values = tuple(rng.randbelow(6) + 1 for _ in range(count))
        dice.extend(values)
        return sum(values)

    def health() -> CheckTrace:
        check = success_roll("gurps-basic-set-4e-2004", ht, check_modifiers, rng=rng)
        checks.append(check)
        return check

    effect = FrightEffect(table_total=total)
    # Numeric table entries, with explicit units and typed choice requirements.
    if total <= 11 or total in (14, 15, 16):
        seconds = 1 if total <= 9 else roll(2 if total == 11 else 1)
        recovery: Literal["none", "ht", "will", "modified-will"] = (
            "none" if total <= 5 else "will" if total <= 7 else "modified-will"
        )
        effect = effect.model_copy(
            update={
                "condition": "stunned",
                "duration_seconds": seconds,
                "recovery_attribute": recovery,
                "recovery_interval_seconds": 1 if recovery != "none" else 0,
            }
        )
        if total in (14, 15):
            effect = effect.model_copy(update={"fp_loss": roll(1)})
        if total == 16:
            effect = effect.model_copy(update={"trait_choice": "quirk", "trait_points": -1})
    elif total == 12:
        effect = effect.model_copy(
            update={
                "condition": "retching",
                "duration_seconds": max(0, 25 - ht),
                "recovery_attribute": "ht",
                "recovery_interval_seconds": 1,
            }
        )
    elif total in (13, 22, 23, 24, 25, 34, 35, 36, 37):
        choices = {
            13: ("quirk", -1),
            22: ("delusion", -10),
            23: ("mental", -10),
            24: ("physical", -15),
            25: ("worsen-self-control", -10),
            34: ("delusion", -15),
            35: ("mental", -15),
            36: ("physical", -20),
            37: ("physical", -30),
        }
        choice, points = choices[total]
        effect = effect.model_copy(update={"trait_choice": choice, "trait_points": points})
    elif total in (17, 18, 19, 20, 26, 27):
        seconds = 60 * roll(2 if total == 19 else 4 if total == 20 else 1)
        loss = 1 if total == 19 else 0
        if total in (18, 26, 27):
            loss = int(not health().outcome.succeeded)
        effect = effect.model_copy(
            update={
                "condition": "unconscious",
                "duration_seconds": seconds,
                "recovery_attribute": "ht",
                "recovery_interval_seconds": 60,
                "hp_loss": loss,
                "fp_loss": roll(1) if total == 20 else 0,
                "trait_choice": "delusion" if total == 26 else "mental" if total == 27 else "none",
                "trait_points": -10 if total in (26, 27) else 0,
            }
        )
    elif total == 21:
        effect = effect.model_copy(
            update={
                "condition": "panic",
                "duration_seconds": 60 * roll(1),
                "recovery_attribute": "will",
                "recovery_interval_seconds": 60,
            }
        )
    elif total == 28 or total == 29 or total >= 38:
        seconds = 1800 if total == 28 else 3600 * roll(1)
        effect = effect.model_copy(
            update={
                "condition": "unconscious",
                "duration_seconds": seconds,
                "recovery_attribute": "ht",
                "recovery_interval_seconds": 1800 if total == 28 else 0,
                "repeat_duration_dice": 0 if total == 28 else 1,
                "repeat_duration_unit": 3600,
                "aftermath_penalty": -2,
                "aftermath_seconds": 21600,
                "trait_choice": "delusion" if total == 38 else "mental" if total >= 39 else "none",
                "trait_points": -15 if total >= 38 else 0,
                "permanent_iq_loss": 1 if total >= 40 else 0,
            }
        )
    elif total == 30:
        seconds = 86400 * roll(1)
        effect = effect.model_copy(
            update={
                "condition": "catatonia",
                "duration_seconds": seconds,
                "recovery_attribute": "ht",
                "repeat_duration_dice": 1,
                "repeat_duration_unit": 86400,
                "aftermath_penalty": -2,
                "aftermath_seconds": seconds,
                "neglect_progression": True,
            }
        )
    elif total == 31:
        seconds, fatigue = 60 * roll(1), roll(1)
        trace = health()
        effect = effect.model_copy(
            update={
                "condition": "seizure",
                "duration_seconds": seconds,
                "fp_loss": fatigue,
                "hp_loss": roll(1) if not trace.outcome.succeeded else 0,
                "permanent_ht_loss": int(trace.outcome is Outcome.CRITICAL_FAILURE),
            }
        )
    elif total == 32:
        effect = effect.model_copy(update={"hp_loss": roll(2)})
    elif total == 33:
        effect = effect.model_copy(
            update={"condition": "panic", "panic_severity": roll(3), "recovery_attribute": "will"}
        )
    return FrightEffect.model_validate(
        effect.model_copy(update={"dice": tuple(dice), "checks": tuple(checks)})
    )
