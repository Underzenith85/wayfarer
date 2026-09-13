"""Complete selected-printing mundane trait catalog and typed runtime projection.

The exhaustive identities and printed values come from the frozen Characters
third-printing source ledger.  This module turns only the mundane rows assigned
to #680/#681 into executable definitions.  Exotic and supernatural rows remain
owned by their existing family modules.

Variable constructions are deliberately explicit: a purchase must select one
of the source-bounded point values.  The compiler never accepts a client formula
or silently chooses a variable construction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, cast

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.traits.base import TraitOptions, TraitParameter, cost
from wayfarer.errors import ValidationError

PROFILE: Final = "gurps-basic-set-4e-2004"
HOOK_PREFIX: Final = "mundane-trait:"
POINT_COST_PARAMETER: Final = "point-cost"

# Parent rows whose list-page value is "Variable" (or whose final alternative
# was clipped across a list column) need an explicit selected-printing domain.
# These are construction totals, not formulas supplied by a caller.
POINT_COST_OVERRIDES: Final = MappingProxyType(
    {
        "trait:advantage:appearance": (4, 5, 6, 8, 10, 12, 15, 16, 20, 25),
        "trait:advantage:enhanced-defenses": (5, 10, 15),
        "trait:advantage:legal-enforcement-powers": (5, 10, 15),
        "trait:advantage:luck": (15, 30, 60),
        "trait:advantage:security-clearance": (5, 10, 15),
        "trait:advantage:talent": (5, 10, 15),
        "trait:advantage:wealth": (10, 20, 30, 50, 75, 100),
        "trait:advantage:weapon-master": (20, 25, 30, 35, 40, 45),
        "trait:disadvantage:addiction": (-5, -10, -15, -20, -25, -30, -35, -40),
        "trait:disadvantage:appearance": (-4, -8, -16, -20, -24),
        "trait:disadvantage:chronic-pain": (-5, -10, -15, -20, -30),
        "trait:disadvantage:code-of-honor": (-5, -10, -15),
        "trait:disadvantage:delusions": (-1, -5, -10, -15),
        "trait:disadvantage:disciplines-of-faith": (-5, -10, -15),
        "trait:disadvantage:flashbacks": (-5, -10, -20),
        "trait:disadvantage:g-intolerance": (-10, -20),
        "trait:disadvantage:insomniac": (-10, -15),
        "trait:disadvantage:intolerance": (-5, -10),
        "trait:disadvantage:lame": (-10, -15, -20, -30),
        "trait:disadvantage:neurological-disorder": (-15, -30, -60),
        "trait:disadvantage:odious-personal-habits": (-5, -10, -15),
        "trait:disadvantage:pacifism": (-5, -10, -15, -30),
        "trait:disadvantage:phantom-voices": (-5, -10, -15),
        "trait:disadvantage:restricted-diet": (-10, -20, -30, -40),
        "trait:disadvantage:secret": (-5, -10, -20, -30),
        "trait:disadvantage:sense-of-duty": (-2, -5, -10, -15, -20),
        "trait:disadvantage:shyness": (-5, -10, -20),
        "trait:disadvantage:social-stigma": (-5, -10, -15, -20),
        "trait:disadvantage:terminally-ill": (-50, -75, -100),
        "trait:disadvantage:trademark": (-1, -5, -10, -15),
        "trait:disadvantage:vow": (-5, -10, -15),
        "trait:disadvantage:wealth": (-10, -15, -25),
    }
)

TraitKind = Literal["advantage", "disadvantage"]
TraitFamily = Literal[
    "behavior",
    "defense",
    "knowledge",
    "luck",
    "movement",
    "physiology",
    "recovery",
    "relationship",
    "resources",
    "senses",
    "social",
    "talent",
    "technology",
]


@dataclass(frozen=True, slots=True)
class CompleteTraitSpec:
    id: str
    title: str
    page: int
    kind: TraitKind
    family: TraitFamily
    point_cost: int
    maximum_level: int = 1
    self_control: bool = False
    point_cost_choices: tuple[int, ...] = ()
    owner_issue: int = 680

    @property
    def hook(self) -> str:
        return HOOK_PREFIX + self.family

    @property
    def parameters(self) -> tuple[TraitParameter, ...]:
        if not self.point_cost_choices:
            return ()
        return (TraitParameter(POINT_COST_PARAMETER, "integer", self.point_cost_choices),)


@dataclass(frozen=True, slots=True)
class MundaneTraitEffect:
    """An immutable consequence routed to the named family-local service."""

    definition_id: str
    family: TraitFamily
    levels: int
    point_cost: int
    parameters: tuple[tuple[str, str | int | bool], ...]
    requires_player_choice: bool


def _catalog_path() -> Path:
    return Path(__file__).with_name("complete_catalog.json")


def _family(title: str) -> TraitFamily:
    value = title.casefold()
    groups: tuple[tuple[TraitFamily, tuple[str, ...]], ...] = (
        ("relationship", ("allies", "contact", "dependent", "enemy", "patron", "duty")),
        ("senses", ("vision", "sight", "hearing", "deaf", "smell", "taste", "depth")),
        ("movement", ("balance", "jointed", "flexibility", "g-tolerance", "lame", "klutz")),
        ("recovery", ("healing", "sleep", "pain", "terminally ill", "hemophilia", "wounded")),
        ("technology", ("tl", "cybernetics", "gizmo", "calculator", "mathematician")),
        ("resources", ("wealth", "income", "debt", "signature gear", "maintenance")),
        ("luck", ("luck", "serendipity", "intuition", "common sense", "daredevil")),
        ("talent", ("talent", "ability", "artist", "artificer", "outdoorsman", "healer")),
        ("defense", ("defense", "gunslinger", "weapon master", "trained by a master")),
        (
            "social",
            (
                "rank",
                "status",
                "reputation",
                "appearance",
                "regard",
                "stigma",
                "voice",
                "face",
                "hospitality",
                "identity",
                "legal",
                "clearance",
                "tenure",
                "chameleon",
                "pitiable",
            ),
        ),
        (
            "physiology",
            (
                "consumption",
                "alcohol",
                "back",
                "grip",
                "float",
                "speak",
                "arm",
                "hand",
                "digit",
                "fat",
                "skinny",
                "dwarfism",
                "gigantism",
                "hunchback",
                "sickness",
                "epilepsy",
                "neurological",
                "numb",
                "stuttering",
                "susceptible",
            ),
        ),
        ("knowledge", ("memory", "learn", "dyslexia", "innumerate", "iconographic")),
    )
    return next(
        (family for family, words in groups if any(word in value for word in words)), "behavior"
    )


def _cost_shape(
    kind: TraitKind, classification: str, listed: str
) -> tuple[int, int, bool, tuple[int, ...]]:
    combined = f"{classification} {listed}"
    numbers = tuple(dict.fromkeys(int(value) for value in re.findall(r"(?<![\w.])-?\d+", combined)))
    self_control = "*" in combined
    per_level = any(
        marker in combined.casefold()
        for marker in ("/level", "/attack", "/gizmo", "/culture", "/feature")
    )
    variable = "variable" in combined.casefold()
    alternatives = " or " in f" {combined.casefold()} " or " to " in f" {combined.casefold()} "
    if variable and not numbers:
        choices = tuple(range(1, 101)) if kind == "advantage" else tuple(range(-100, 0))
    elif alternatives:
        choices = numbers
        if " to " in f" {combined.casefold()} " and len(numbers) >= 2:
            low, high = min(numbers), max(numbers)
            choices = tuple(range(low, high + 1))
    else:
        choices = ()
    if not numbers and not choices:
        raise ValidationError(f"Mundane trait has no selected-printing point value: {listed}")
    base = choices[0] if choices else numbers[0]
    maximum = 100 if per_level else 1
    return base, maximum, self_control, choices


def _load_specs() -> tuple[CompleteTraitSpec, ...]:
    rows = json.loads(_catalog_path().read_text())["rows"]
    result: list[CompleteTraitSpec] = []
    for row in rows:
        identifier = cast(str, row["id"])
        if not identifier.startswith(("trait:advantage:", "trait:disadvantage:")):
            continue
        classification = cast(str, row["classification"])
        # The final classification field is the source marker.  Only mundane
        # rows (an en dash in the selected ledger) belong to these issues.
        if classification.split("|")[2].split()[0] not in {"–", "-"}:
            continue
        kind: TraitKind = (
            "advantage" if identifier.startswith("trait:advantage:") else "disadvantage"
        )
        base, maximum, self_control, choices = _cost_shape(
            kind, classification, cast(str, row["listed_value"])
        )
        choices = POINT_COST_OVERRIDES.get(identifier, choices)
        if choices:
            base = choices[0]
        result.append(
            CompleteTraitSpec(
                identifier,
                cast(str, row["title"]),
                cast(int, row["printed_page"]),
                kind,
                _family(cast(str, row["title"])),
                base,
                maximum,
                self_control,
                choices,
                cast(int, row["owner_issue"]),
            )
        )
    specs = tuple(result)
    counts = {
        kind: sum(spec.kind == kind for spec in specs) for kind in ("advantage", "disadvantage")
    }
    if counts != {"advantage": 89, "disadvantage": 178}:
        raise ValidationError(f"Mundane trait ledger drift: {counts}")
    if len({spec.id for spec in specs}) != len(specs):
        raise ValidationError("Duplicate complete mundane trait identifier")
    return specs


SPECS: Final = _load_specs()
SPEC_BY_ID: Final = MappingProxyType({spec.id: spec for spec in SPECS})
HOOKS: Final = frozenset(spec.hook for spec in SPECS)


def validate_purchase(definition: RuleDefinition, levels: int, options: TraitOptions) -> int:
    """Price one exact construction and reject omitted contextual selections."""

    try:
        spec = SPEC_BY_ID[definition.id]
    except KeyError as exc:
        raise ValidationError("Unknown complete mundane trait") from exc
    if definition.trait_rules is None or spec.hook not in definition.trait_rules.runtime_hooks:
        raise ValidationError("Mundane trait definition is not bound to its owning family")
    selected_cost = spec.point_cost
    if spec.point_cost_choices:
        supplied = dict(options.parameters)
        if set(supplied) != {POINT_COST_PARAMETER} or len(options.parameters) != 1:
            raise ValidationError("Variable trait requires an explicit point-cost selection")
        value = supplied[POINT_COST_PARAMETER]
        if type(value) is not int or value not in spec.point_cost_choices:
            raise ValidationError("Point-cost selection is outside the source-bounded variants")
        selected_cost = value
    priced = replace(definition, point_cost=selected_cost)
    assert priced.trait_rules is not None
    return cost(selected_cost, levels, options, priced.trait_rules)


def effects(
    definition_ids: tuple[tuple[str, int, TraitOptions], ...],
) -> tuple[MundaneTraitEffect, ...]:
    """Project approved purchases for dispatch by family-local services.

    Optional in-play outcomes remain requests: this projection records that a
    player choice is required and never makes that choice for the character.
    """

    projected = []
    for identifier, levels, options in definition_ids:
        spec = SPEC_BY_ID.get(identifier)
        if spec is None:
            continue
        point_value = dict(options.parameters).get(POINT_COST_PARAMETER, spec.point_cost)
        assert isinstance(point_value, int)
        projected.append(
            MundaneTraitEffect(
                identifier,
                spec.family,
                levels,
                point_value,
                options.parameters,
                spec.family in {"behavior", "luck", "relationship"},
            )
        )
    return tuple(projected)
