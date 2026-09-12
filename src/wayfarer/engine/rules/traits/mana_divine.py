"""Pinned Basic Set mana and divine trait construction (Characters B66-143)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
    SourceReference,
)
from wayfarer.engine.rules.traits.base import (
    TraitModifier,
    TraitOptions,
    TraitParameter,
    TraitRules,
    cost,
)
from wayfarer.errors import ValidationError

PROFILE: Final = "gurps-basic-set-4e-2004"
SOURCE_ID: Final = "sjg:basic-set-characters-4e-2004"
PACKAGE_ID: Final = "package:gurps-basic-mana-divine-traits"


@dataclass(frozen=True, slots=True)
class ManaDivineBinding:
    id: str
    name: str
    point_cost: int
    maximum_level: int
    parameters: tuple[TraitParameter, ...] = ()
    modifiers: tuple[TraitModifier, ...] = ()

    @property
    def hook(self) -> str:
        return "mana-divine-trait:" + self.id.split(":", 1)[1]


def modifier(identifier: str, percent: int, exclusions: tuple[str, ...] = ()) -> TraitModifier:
    return TraitModifier(identifier, percent, exclusions, "mana-divine-trait:" + identifier)


MAGERY_LIMITATIONS: Final = (
    modifier("dance", -40),
    modifier("dark-aspected", -50, ("day-aspected", "night-aspected")),
    modifier("day-aspected", -40, ("dark-aspected", "night-aspected")),
    modifier("musical", -50, ("song",)),
    modifier("night-aspected", -40, ("dark-aspected", "day-aspected")),
    modifier("one-college", -40),
    modifier("solitary", -40),
    modifier("song", -40, ("musical",)),
)
AREA_EFFECT_IDS: Final = tuple(f"area-effect-{level}" for level in range(1, 21))
AREA_EFFECTS: Final = tuple(
    modifier(
        identifier, 50 * level, tuple(value for value in AREA_EFFECT_IDS if value != identifier)
    )
    for level, identifier in enumerate(AREA_EFFECT_IDS, 1)
)
COLLEGES: Final = (
    "all",
    "air",
    "body-control",
    "communication-empathy",
    "earth",
    "enchantment",
    "fire",
    "gate",
    "healing",
    "knowledge",
    "light-darkness",
    "meta",
    "mind-control",
    "movement",
    "necromantic",
    "protection-warning",
    "water",
)


BINDINGS: Final = (
    ManaDivineBinding(
        "advantage:magery",
        "Magery",
        10,
        100,
        (
            TraitParameter("zero-only", "boolean", (False, True)),
            TraitParameter("college", "text", COLLEGES),
        ),
        MAGERY_LIMITATIONS,
    ),
    ManaDivineBinding(
        "advantage:magic-resistance",
        "Magic Resistance",
        2,
        100,
        modifiers=(modifier("improved", 150),),
    ),
    ManaDivineBinding(
        "advantage:mana-damper",
        "Mana Damper",
        10,
        3,
        modifiers=AREA_EFFECTS + (modifier("switchable", 100),),
    ),
    ManaDivineBinding(
        "advantage:mana-enhancer",
        "Mana Enhancer",
        50,
        2,
        modifiers=AREA_EFFECTS + (modifier("switchable", 100),),
    ),
    ManaDivineBinding("advantage:power-investiture", "Power Investiture", 10, 100),
    ManaDivineBinding("disadvantage:magic-susceptibility", "Magic Susceptibility", -3, 5),
)
BINDING_BY_ID: Final = {binding.id: binding for binding in BINDINGS}
RUNTIME_HOOKS: Final = frozenset(
    {
        *(binding.hook for binding in BINDINGS),
        *(
            selected.runtime_hook
            for binding in BINDINGS
            for selected in binding.modifiers
            if selected.runtime_hook is not None
        ),
    }
)


def metadata(binding: ManaDivineBinding) -> TraitRules:
    return TraitRules(
        PROFILE,
        binding.maximum_level,
        parameters=binding.parameters,
        modifiers=binding.modifiers,
        runtime_hooks=(binding.hook,),
    )


def definition(binding: ManaDivineBinding) -> RuleDefinition:
    return RuleDefinition(
        binding.id,
        DefinitionKind.TRAIT,
        binding.name,
        SOURCE_ID,
        binding.point_cost,
        ImplementationStatus.IMPLEMENTED,
        parameters=tuple(value.name for value in binding.parameters),
        hooks=("supernatural", "mana-divine-trait"),
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
                "B66-68/B77/B143",
            ),
        ),
        tuple(definition(binding) for binding in BINDINGS),
    )


def validate_purchase(entry: RuleDefinition, levels: int, options: TraitOptions) -> int:
    binding = BINDING_BY_ID.get(entry.id)
    if (
        binding is None
        or entry.trait_rules != metadata(binding)
        or binding.hook not in entry.trait_rules.runtime_hooks
    ):
        raise ValidationError("Mana/divine construction differs from its runtime binding")
    priced = cost(binding.point_cost, levels, options, metadata(binding))
    if binding.id == "advantage:magery":
        parameters = dict(options.parameters)
        zero_only = bool(parameters["zero-only"])
        one_college = "one-college" in options.modifiers
        if (parameters["college"] != "all") != one_college:
            raise ValidationError("Magery college must match One College Only")
        if zero_only and levels != 1:
            raise ValidationError("Magery 0 is not leveled")
        if zero_only and (levels != 1 or options.modifiers):
            raise ValidationError("Magery 0 cannot carry leveled Magery limitations")
        return 5 if zero_only else priced + 5
    return priced
