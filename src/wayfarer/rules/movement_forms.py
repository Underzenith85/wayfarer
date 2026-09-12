"""Basic Set movement and body-form trait construction.

The registry is an executable package boundary, not a name-based permission.
Costs and bounded choices are transcribed from Characters, Fourth Edition,
third printing, B34-97 and B129-165.  Variable constructions are priced here
instead of accepting caller-supplied arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Final, Literal

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
    SourceReference,
)
from wayfarer.rules.traits import TraitModifier, TraitOptions, TraitParameter, TraitRules, cost

PROFILE: Final = "gurps-basic-set-4e-2004"
SOURCE_ID: Final = "sjg:basic-set-characters-4e-2004"
PACKAGE_ID: Final = "package:gurps-basic-movement-forms"


@dataclass(frozen=True, slots=True)
class MovementFormBinding:
    id: str
    name: str
    point_cost: int
    maximum_level: int = 1
    parameters: tuple[TraitParameter, ...] = ()
    modifiers: tuple[TraitModifier, ...] = ()
    exclusions: tuple[str, ...] = ()
    manual: bool = False

    @property
    def hook(self) -> str:
        return "movement-form:" + self.id.split(":", 1)[1]


def parameter(
    name: str, kind: Literal["text", "integer", "boolean"], *choices: str | int | bool
) -> TraitParameter:
    return TraitParameter(name, kind, choices)


def modifier(identifier: str, percent: int, *exclusions: str) -> TraitModifier:
    return TraitModifier(identifier, percent, exclusions, "movement-form:" + identifier)


MOVE_MODE = parameter("mode", "text", "ground", "air", "water", "space")
ARM_SCOPE = parameter("scope", "text", "one-arm", "all-arms")
ARM_ST_SCOPE = parameter("scope", "text", "one-arm", "two-arms", "three-or-more-arms")
LEG_COUNT = parameter("legs", "integer", 3, 4, 5, 6, 7, 8)
NO_LEGS_KIND = parameter(
    "form",
    "text",
    "aerial",
    "aquatic",
    "bounces",
    "portable",
    "sessile",
    "slithers",
    "tracks-or-wheels",
)
TEMPLATE_COSTS = (
    parameter("native-template-cost", "integer", *range(-500, 501)),
    parameter("target-template-cost", "integer", *range(-500, 501)),
)

FLIGHT_MODIFIERS = (
    modifier("cannot-hover", -15),
    modifier("controlled-gliding", -45, "gliding"),
    modifier("gliding", -50, "controlled-gliding"),
    modifier("lighter-than-air", -10),
    modifier("low-ceiling-10", -10, "low-ceiling-20", "low-ceiling-30"),
    modifier("low-ceiling-20", -20, "low-ceiling-10", "low-ceiling-30"),
    modifier("low-ceiling-30", -25, "low-ceiling-10", "low-ceiling-20"),
    modifier("space-flight", 50),
    modifier("winged", -25),
)
INSUBSTANTIAL_MODIFIERS = (
    modifier("affects-substantial", 100),
    modifier(
        "can-carry-no-encumbrance", 10, "can-carry-light", "can-carry-medium", "can-carry-heavy"
    ),
    modifier(
        "can-carry-light", 20, "can-carry-no-encumbrance", "can-carry-medium", "can-carry-heavy"
    ),
    modifier(
        "can-carry-medium", 50, "can-carry-no-encumbrance", "can-carry-light", "can-carry-heavy"
    ),
    modifier(
        "can-carry-heavy", 100, "can-carry-no-encumbrance", "can-carry-light", "can-carry-medium"
    ),
    modifier("partial-change", 20),
)


BINDINGS: Final = (
    MovementFormBinding(
        "advantage:360-vision",
        "360° Vision",
        25,
        modifiers=(
            modifier("easy-to-hit", -20),
            modifier("panoptic-1", 20),
            modifier("panoptic-2", 40),
        ),
    ),
    MovementFormBinding(
        "advantage:duplication",
        "Duplication",
        35,
        100,
        modifiers=(
            modifier("digital", -60),
            modifier("duplicated-gear", 100),
            modifier("no-sympathetic-injury", 20),
        ),
    ),
    MovementFormBinding("advantage:elastic-skin", "Elastic Skin", 20),
    MovementFormBinding("advantage:altered-time-rate", "Altered Time Rate", 100, 100),
    MovementFormBinding(
        "advantage:alternate-form",
        "Alternate Form",
        15,
        parameters=TEMPLATE_COSTS,
        modifiers=(
            modifier("absorptive-change", 5),
            modifier("active-change", 20),
            modifier("once-on-stays-on", -50),
        ),
    ),
    MovementFormBinding(
        "advantage:enhanced-move",
        "Enhanced Move",
        20,
        100,
        (MOVE_MODE,),
        (
            modifier("all-out-only", -20),
            modifier("handling-penalty-1", -5),
            modifier("road-bound", -50),
        ),
    ),
    MovementFormBinding("advantage:enhanced-time-sense", "Enhanced Time Sense", 45),
    MovementFormBinding("advantage:amphibious", "Amphibious", 10),
    MovementFormBinding("advantage:enhanced-tracking", "Enhanced Tracking", 5, 100),
    MovementFormBinding(
        "advantage:extra-arms",
        "Extra Arms",
        10,
        100,
        modifiers=(
            modifier("extra-flexible", 50),
            modifier("foot-manipulator", -30),
            modifier("long", 100),
            modifier("short", -50),
            modifier("weapon-mount", -80),
        ),
    ),
    MovementFormBinding("advantage:arm-dx", "Arm DX", 12, 100, (ARM_SCOPE,)),
    MovementFormBinding("advantage:extra-head", "Extra Head", 15, 100),
    MovementFormBinding("advantage:arm-st", "Arm ST", 3, 100, (ARM_ST_SCOPE,)),
    MovementFormBinding(
        "advantage:extra-legs",
        "Extra Legs",
        5,
        parameters=(LEG_COUNT,),
        modifiers=(modifier("cannot-kick", -50), modifier("long", 100)),
    ),
    MovementFormBinding("advantage:extra-mouth", "Extra Mouth", 5, 100),
    MovementFormBinding("advantage:brachiator", "Brachiator", 5),
    MovementFormBinding("advantage:catfall", "Catfall", 10),
    MovementFormBinding("advantage:flight", "Flight", 40, modifiers=FLIGHT_MODIFIERS),
    MovementFormBinding(
        "advantage:growth", "Growth", 10, 8, modifiers=(modifier("maximum-size-only", -40),)
    ),
    MovementFormBinding(
        "advantage:clinging", "Clinging", 20, modifiers=(modifier("attraction", -60),)
    ),
    MovementFormBinding("advantage:hermaphromorph", "Hermaphromorph", 5),
    MovementFormBinding(
        "advantage:insubstantiality", "Insubstantiality", 80, modifiers=INSUBSTANTIAL_MODIFIERS
    ),
    MovementFormBinding(
        "advantage:shadow-form", "Shadow Form", 50, modifiers=(modifier("three-dimensional", 20),)
    ),
    MovementFormBinding("advantage:shapeshifting", "Shapeshifting", 0, manual=True),
    MovementFormBinding(
        "advantage:shrinking", "Shrinking", 5, 8, modifiers=(modifier("full-damage", 100),)
    ),
    MovementFormBinding("advantage:lifting-st", "Lifting ST", 3, 100),
    MovementFormBinding("advantage:slippery", "Slippery", 2, 5),
    MovementFormBinding(
        "advantage:stretching", "Stretching", 6, 20, modifiers=(modifier("force-extension", -10),)
    ),
    MovementFormBinding(
        "advantage:morph",
        "Morph",
        100,
        parameters=TEMPLATE_COSTS,
        modifiers=(
            modifier("cosmetic", -50),
            modifier("mass-conservation", -20),
            modifier("retain-shape", -20),
            modifier("unlimited", 50),
        ),
    ),
    MovementFormBinding("advantage:super-climbing", "Super Climbing", 3, 100),
    MovementFormBinding("advantage:super-jump", "Super Jump", 10, 100),
    MovementFormBinding(
        "advantage:telekinesis",
        "Telekinesis",
        5,
        100,
        modifiers=(
            modifier("animation", -30),
            modifier("magnetic", -50),
            modifier("psychokinetic", -10),
            modifier("visible", -20),
        ),
    ),
    MovementFormBinding(
        "advantage:payload", "Payload", 1, 100, modifiers=(modifier("exposed", -50),)
    ),
    MovementFormBinding(
        "advantage:terrain-adaptation",
        "Terrain Adaptation",
        5,
        parameters=(
            parameter("native", "boolean", False, True),
            parameter("terrain", "text", "ice", "mud", "sand", "snow", "water"),
        ),
    ),
    MovementFormBinding(
        "advantage:permeation",
        "Permeation",
        40,
        parameters=(parameter("rarity", "text", "very-common", "common", "occasional", "rare"),),
        modifiers=(modifier("can-carry-objects", 20), modifier("meld", 150)),
    ),
    MovementFormBinding(
        "advantage:tunneling", "Tunneling", 30, 100, modifiers=(modifier("hands-free", 20),)
    ),
    MovementFormBinding("advantage:walk-on-air", "Walk on Air", 20),
    MovementFormBinding("advantage:walk-on-liquid", "Walk on Liquid", 15),
    MovementFormBinding("disadvantage:horizontal", "Horizontal", -10),
    MovementFormBinding("disadvantage:invertebrate", "Invertebrate", -20),
    MovementFormBinding("disadvantage:decreased-time-rate", "Decreased Time Rate", -100),
    MovementFormBinding("disadvantage:shadow-form", "Shadow Form", -20),
    MovementFormBinding("disadvantage:no-fine-manipulators", "No Fine Manipulators", -30),
    MovementFormBinding("disadvantage:no-legs", "No Legs", 0, parameters=(NO_LEGS_KIND,)),
    MovementFormBinding("disadvantage:no-manipulators", "No Manipulators", -50),
    MovementFormBinding("disadvantage:semi-upright", "Semi-Upright", -5),
    MovementFormBinding("disadvantage:sexless", "Sexless", -1),
)
BINDING_BY_ID: Final = {binding.id: binding for binding in BINDINGS}
RUNTIME_HOOKS: Final = frozenset(
    {
        *(binding.hook for binding in BINDINGS if not binding.manual),
        *(
            selected.runtime_hook
            for binding in BINDINGS
            for selected in binding.modifiers
            if selected.runtime_hook is not None
        ),
    }
)


def metadata(binding: MovementFormBinding) -> TraitRules:
    return TraitRules(
        PROFILE,
        maximum_level=binding.maximum_level,
        parameters=binding.parameters,
        modifiers=binding.modifiers,
        runtime_hooks=(binding.hook,),
    )


def definition(binding: MovementFormBinding) -> RuleDefinition:
    return RuleDefinition(
        binding.id,
        DefinitionKind.TRAIT,
        binding.name,
        SOURCE_ID,
        binding.point_cost,
        ImplementationStatus.MANUAL if binding.manual else ImplementationStatus.IMPLEMENTED,
        exclusions=binding.exclusions,
        parameters=tuple(p.name for p in binding.parameters),
        hooks=("supernatural", "movement-form"),
        trait_rules=metadata(binding),
    )


def package() -> RulesPackage:
    return RulesPackage(
        PACKAGE_ID,
        "1.0.0",
        "gurps-4e",
        (
            SourceReference(
                SOURCE_ID,
                "GURPS Basic Set: Characters, 4e, third printing",
                "user-supplied-reference",
                "B34-97, B129-165",
            ),
        ),
        tuple(definition(binding) for binding in BINDINGS),
    )


def _parameters(options: TraitOptions) -> dict[str, str | int | bool]:
    return dict(options.parameters)


def purchase_cost(binding: MovementFormBinding, levels: int, options: TraitOptions) -> int:
    """Return trusted special-case pricing, then the shared modifier calculation."""
    if binding.manual:
        raise ValidationError("Shapeshifting is a heading; purchase Alternate Form or Morph")
    # Shared validation also rejects missing/unknown parameters and modifiers.
    ordinary = cost(binding.point_cost, levels, options, metadata(binding))
    values = _parameters(options)
    if binding.id in {"advantage:alternate-form", "advantage:morph"}:
        native, target = int(values["native-template-cost"]), int(values["target-template-cost"])
        premium = max(
            0,
            int(
                (Decimal(target - native) * Decimal("0.9")).to_integral_value(
                    rounding=ROUND_CEILING
                )
            ),
        )
        base = 15 + premium if binding.id.endswith("alternate-form") else max(100, premium)
        percent = max(-80, sum(m.percent for m in binding.modifiers if m.id in options.modifiers))
        return int(
            (Decimal(base) * Decimal(100 + percent) / 100).to_integral_value(rounding=ROUND_CEILING)
        )
    if binding.id == "advantage:arm-dx":
        return (12 if values["scope"] == "one-arm" else 16) * levels
    if binding.id == "advantage:arm-st":
        return {"one-arm": 3, "two-arms": 5, "three-or-more-arms": 8}[str(values["scope"])] * levels
    if binding.id == "advantage:extra-legs":
        ordinary = {3: 5, 4: 10, 5: 15, 6: 20, 7: 25, 8: 30}[int(values["legs"])]
    elif binding.id == "advantage:terrain-adaptation":
        ordinary = 0 if values["native"] else 5
    elif binding.id == "advantage:permeation":
        ordinary = {"very-common": 80, "common": 40, "occasional": 20, "rare": 10}[
            str(values["rarity"])
        ]
    elif binding.id == "advantage:tunneling":
        ordinary = 25 + 5 * levels
    elif binding.id == "disadvantage:no-legs":
        ordinary = {
            "aerial": 0,
            "aquatic": 0,
            "bounces": -5,
            "portable": -30,
            "sessile": -50,
            "slithers": 0,
            "tracks-or-wheels": -20,
        }[str(values["form"])]
    return ordinary


def validate_purchase(entry: RuleDefinition, levels: int, options: TraitOptions) -> int:
    binding = BINDING_BY_ID.get(entry.id)
    if (
        binding is None
        or entry.trait_rules != metadata(binding)
        or binding.hook not in entry.trait_rules.runtime_hooks
    ):
        raise ValidationError("Movement/form construction differs from its runtime binding")
    return purchase_cost(binding, levels, options)
