"""Selected Wealth, Status, Rank, language and culture rules (Characters B23-30)."""

from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal

from pydantic import Field

from wayfarer.errors import ValidationError
from wayfarer.models import Record

Comprehension = Literal["none", "broken", "accented", "native"]
Disposition = Literal["friendly", "neutral", "angry", "resentful"]

WEALTH_MULTIPLIERS: Final = MappingProxyType(
    {
        "dead-broke": Fraction(0),
        "poor": Fraction(1, 5),
        "struggling": Fraction(1, 2),
        "average": Fraction(1),
        "comfortable": Fraction(2),
        "wealthy": Fraction(5),
        "very-wealthy": Fraction(20),
        "filthy-rich": Fraction(100),
        "multimillionaire-1": Fraction(1000),
        "multimillionaire-2": Fraction(10000),
        "multimillionaire-3": Fraction(100000),
    }
)
LEVELS: Final = ("none", "broken", "accented", "native")


class LanguageAbility(Record):
    language_id: str
    spoken: Comprehension = "none"
    written: Comprehension = "none"

    def skill_penalty(self, mode: Literal["spoken", "written"], *, artistic: bool = False) -> int:
        level = self.spoken if mode == "spoken" else self.written
        value = {"none": -100, "broken": -3, "accented": -1, "native": 0}[level]
        return value * 2 if artistic and value > -100 else value


class BackgroundTraits(Record):
    wealth: str = "average"
    purchased_status: int = Field(default=0, ge=-2, le=8)
    free_status: int = Field(default=0, ge=0)
    ranks: tuple[tuple[str, int, str], ...] = ()
    languages: tuple[LanguageAbility, ...] = ()
    cultures: tuple[str, ...] = ()

    @property
    def status(self) -> int:
        return self.purchased_status + self.free_status

    def starting_assets(self, average: int) -> Fraction:
        if type(average) is not int or average < 0:
            raise ValidationError("Average starting wealth must be a nonnegative integer")
        return average * WEALTH_MULTIPLIERS[self.wealth]

    def status_reaction(self, observer_status: int, disposition: Disposition) -> int:
        difference = self.status - observer_status
        if self.status < 0 and difference < 0:
            return max(-4, difference)
        if difference > 0:
            return 0 if disposition == "resentful" else difference
        if difference < 0 and disposition not in ("friendly",):
            return difference
        return 0


BACKGROUND_BINDINGS: Final = MappingProxyType(
    {
        **{f"trait:wealth-{key}": ("trait.wealth", "wealth", key) for key in WEALTH_MULTIPLIERS},
        "trait:status": ("trait.status", "purchased_status", 1),
        "trait:low-status": ("trait.status", "purchased_status", -1),
        "trait:language-talent": ("trait.language_talent", "language_talent", True),
    }
)
BACKGROUND_HOOKS: Final = frozenset(
    {
        "trait.wealth",
        "trait.status",
        "trait.rank",
        "trait.language",
        "trait.language_talent",
        "trait.culture",
    }
)


def comprehension(points: int, *, talent: bool) -> Comprehension:
    if type(points) is not int or not 0 <= points <= 3:
        raise ValidationError("Language-form points must be within 0..3")
    effective = points + int(talent and points > 0)
    return LEVELS[effective]


def free_status(wealth: str, ranks: tuple[tuple[str, int, str], ...], linked: bool) -> int:
    wealth_value = 0
    if linked:
        wealth_value = (
            3
            if wealth in ("multimillionaire-2", "multimillionaire-3")
            else 2
            if wealth == "multimillionaire-1"
            else int(wealth in ("wealthy", "very-wealthy", "filthy-rich"))
        )
    rank_value = sum(
        3 if level >= 8 else 2 if level >= 5 else 1 if level >= 2 else 0
        for _, level, kind in ranks
        if kind == "ordinary"
    )
    return wealth_value + rank_value
