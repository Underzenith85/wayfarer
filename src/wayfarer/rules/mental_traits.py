"""Selected mundane mental and relationship rules (Characters 4e, B31-159)."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.rules.checks import RandomSource, draw_dice

MentalTask = Literal[
    "recall-general",
    "recall-detail",
    "learning",
    "lengthy-mental",
    "notice-interruption",
    "creative",
    "social",
    "hearing-penetrating-voice",
    "intimidation-surprise",
]


class MentalTraits(Record):
    memory: Literal["ordinary", "eidetic", "photographic"] = "ordinary"
    single_minded: bool = False
    versatile: bool = False
    shyness: Literal["none", "mild", "severe", "crippling"] = "none"
    penetrating_voice: bool = False
    honesty: bool = False
    truthfulness: bool = False

    def modifier(self, task: MentalTask, *, focused: bool = False, divided: bool = False) -> int:
        if task == "learning":
            return {"ordinary": 0, "eidetic": 5, "photographic": 10}[self.memory]
        if task == "lengthy-mental":
            return 3 if self.single_minded and focused and not divided else 0
        if task == "notice-interruption":
            return -5 if self.single_minded and focused else 0
        if task == "creative":
            return int(self.versatile)
        if task == "social":
            return {"none": 0, "mild": -1, "severe": -2, "crippling": -4}[self.shyness]
        if task == "hearing-penetrating-voice":
            return 3 * int(self.penetrating_voice)
        if task == "intimidation-surprise":
            return int(self.penetrating_voice)
        return 0

    def automatic_recall(self, *, detail: bool) -> bool:
        return self.memory == "photographic" or (self.memory == "eidetic" and not detail)


MENTAL_BINDINGS: Final = MappingProxyType(
    {
        "trait:eidetic-memory": ("trait.memory", "memory", "eidetic"),
        "trait:photographic-memory": ("trait.memory", "memory", "photographic"),
        "trait:single-minded": ("trait.concentration", "single_minded", True),
        "trait:versatile": ("trait.creativity", "versatile", True),
        "trait:shyness-mild": ("trait.shyness", "shyness", "mild"),
        "trait:shyness-severe": ("trait.shyness", "shyness", "severe"),
        "trait:shyness-crippling": ("trait.shyness", "shyness", "crippling"),
        "trait:perk-penetrating-voice": ("trait.penetrating_voice", "penetrating_voice", True),
        "trait:honesty": ("trait.honesty", "honesty", True),
        "trait:truthfulness": ("trait.truthfulness", "truthfulness", True),
    }
)
MENTAL_HOOKS: Final = frozenset(v[0] for v in MENTAL_BINDINGS.values())

CONSEQUENCES: Final = MappingProxyType(
    {
        "trait:bad-temper": "act-against-source-of-stress",
        "trait:curious": "investigate-object-of-curiosity",
        "trait:overconfidence": "act-as-if-more-capable",
        "trait:honesty": "obey-law-and-act-honorably",
        "trait:truthfulness": "tell-truth-or-decline-to-answer",
    }
)


def failed_self_control_obligation(definition_id: str) -> str:
    try:
        return CONSEQUENCES[definition_id]
    except KeyError as exc:
        raise ValidationError("No executable self-control consequence") from exc


Frequency = Literal[6, 9, 12, 15, 18]
RelationshipKind = Literal["ally", "contact", "patron", "dependent", "enemy"]


class Relationship(Record):
    id: str
    person_id: str
    kind: RelationshipKind
    frequency: Frequency = 9
    character_points_percent: int | None = Field(default=None, ge=25, le=150)
    contact_skill: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def exact_shape(self) -> Relationship:
        if not self.id or not self.person_id:
            raise ValueError("A relationship requires stable identities")
        if self.kind in ("ally", "dependent") and self.character_points_percent is None:
            raise ValueError("Ally and Dependent require relative point totals")
        if self.kind == "contact" and self.contact_skill is None:
            raise ValueError("Contact requires its authoritative skill level")
        return self


@dataclass(frozen=True, slots=True)
class AppearanceRoll:
    relationship_id: str
    dice: tuple[int, int, int] | None
    appears: bool


def frequency_roll(relationship: Relationship, rng: RandomSource) -> AppearanceRoll:
    if relationship.frequency == 18:
        return AppearanceRoll(relationship.id, None, True)
    dice = draw_dice(rng)
    return AppearanceRoll(relationship.id, dice, sum(dice) <= relationship.frequency)


def validate_relationships(values: tuple[Relationship, ...]) -> tuple[Relationship, ...]:
    if len({v.id for v in values}) != len(values):
        raise ValidationError("Duplicate relationship ID")
    by_person: dict[str, list[Relationship]] = {}
    for value in values:
        by_person.setdefault(value.person_id, []).append(value)
    for group in by_person.values():
        kinds = {v.kind for v in group}
        if "ally" in kinds and "dependent" in kinds:
            ally = next(v for v in group if v.kind == "ally")
            dependent = next(v for v in group if v.kind == "dependent")
            if ally.frequency != dependent.frequency:
                raise ValidationError("A netted Ally/Dependent must share one frequency")
        elif len(group) > 1:
            raise ValidationError("One person cannot supply duplicate associated-NPC traits")
    return values
