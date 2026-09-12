"""Pinned Basic Set Jumper, Snatcher, and Warp construction (Characters B64-99)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

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
PACKAGE_ID: Final = "package:gurps-basic-world-travel-traits"


@dataclass(frozen=True, slots=True)
class WorldTravelBinding:
    id: str
    name: str
    point_cost: int
    parameters: tuple[TraitParameter, ...] = ()
    modifiers: tuple[TraitModifier, ...] = ()

    @property
    def hook(self) -> str:
        return "world-travel-trait:" + self.id.split(":", 1)[1]


def parameter(
    name: str, kind: Literal["text", "integer", "boolean"], *choices: str | int | bool
) -> TraitParameter:
    return TraitParameter(name, kind, choices)


def modifier(identifier: str, percent: int, *exclusions: str) -> TraitModifier:
    return TraitModifier(identifier, percent, exclusions, "world-travel-trait:" + identifier)


BINDINGS: Final = (
    WorldTravelBinding(
        "advantage:jumper",
        "Jumper",
        100,
        parameters=(parameter("kind", "text", "time", "world"),),
        modifiers=(
            modifier("new-worlds", 50),
            modifier("tracking", 20),
            modifier("tunnel", 100),
            modifier("limited", -20),
        ),
    ),
    WorldTravelBinding(
        "advantage:snatcher",
        "Snatcher",
        80,
        parameters=(parameter("weight", "integer", 5, 10, 20, 40, 80),),
        modifiers=(
            modifier("creation", 100),
            modifier("permanent", 300),
            modifier("specialized", -50),
            modifier("unpredictable", -25),
        ),
    ),
    WorldTravelBinding(
        "advantage:warp",
        "Warp",
        100,
        parameters=(parameter("reliability", "integer", 0, 1, 2, 3, 4, 5, 10),),
        modifiers=(
            modifier("blind", 50),
            modifier("hyperjump", 50),
            modifier("range-limit", -10),
            modifier("tunnel", 40),
        ),
    ),
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


def metadata(binding: WorldTravelBinding) -> TraitRules:
    return TraitRules(
        PROFILE,
        parameters=binding.parameters,
        modifiers=binding.modifiers,
        runtime_hooks=(binding.hook,),
    )


def definition(binding: WorldTravelBinding) -> RuleDefinition:
    return RuleDefinition(
        binding.id,
        DefinitionKind.TRAIT,
        binding.name,
        SOURCE_ID,
        binding.point_cost,
        ImplementationStatus.IMPLEMENTED,
        parameters=tuple(value.name for value in binding.parameters),
        hooks=("supernatural", "world-travel-trait"),
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
                "B64-99",
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
        raise ValidationError("World-travel construction differs from its runtime binding")
    priced = cost(binding.point_cost, levels, options, metadata(binding))
    if binding.id == "advantage:snatcher":
        priced += {5: 0, 10: 8, 20: 16, 40: 24, 80: 32}[int(dict(options.parameters)["weight"])]
    elif binding.id == "advantage:warp":
        priced += 5 * int(dict(options.parameters)["reliability"])
    return priced
