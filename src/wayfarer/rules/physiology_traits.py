"""Pinned Basic Set physiology trait construction (Characters B41-160)."""

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
PACKAGE_ID: Final = "package:gurps-basic-physiology-traits"


@dataclass(frozen=True, slots=True)
class PhysiologyBinding:
    id: str
    name: str
    point_cost: int
    maximum_level: int = 1
    parameters: tuple[TraitParameter, ...] = ()
    modifiers: tuple[TraitModifier, ...] = ()

    @property
    def hook(self) -> str:
        return "physiology-trait:" + self.id.split(":", 1)[1]


def parameter(
    name: str, kind: Literal["text", "integer", "boolean"], *choices: str | int | bool
) -> TraitParameter:
    return TraitParameter(name, kind, choices)


def modifier(identifier: str, percent: int, *exclusions: str) -> TraitModifier:
    return TraitModifier(identifier, percent, exclusions, "physiology-trait:" + identifier)


BINDINGS: Final = (
    PhysiologyBinding(
        "advantage:accessory",
        "Accessory",
        1,
        parameters=(
            parameter("accessory", "text", "built-in-tool", "data-port", "storage-compartment"),
        ),
    ),
    PhysiologyBinding(
        "advantage:doesnt-breathe",
        "Doesn’t Breathe",
        20,
        modifiers=(
            modifier("gills", -50),
            modifier("oxygen-absorption", -25),
            modifier("oxygen-combustion", -50),
            modifier("oxygen-storage", -50),
        ),
    ),
    PhysiologyBinding(
        "advantage:doesnt-eat-or-drink",
        "Doesn’t Eat or Drink",
        10,
        modifiers=(modifier("food-only", -50), modifier("drink-only", -50)),
    ),
    PhysiologyBinding("advantage:doesnt-sleep", "Doesn’t Sleep", 20),
    PhysiologyBinding("advantage:extended-lifespan", "Extended Lifespan", 2, 10),
    PhysiologyBinding(
        "advantage:extra-life",
        "Extra Life",
        25,
        100,
        modifiers=(
            modifier("copy", -20),
            modifier("reincarnation", -20),
            modifier("requires-body", -20),
        ),
    ),
    PhysiologyBinding("advantage:breath-holding", "Breath-Holding", 2, 100),
    PhysiologyBinding("advantage:filter-lungs", "Filter Lungs", 5),
    PhysiologyBinding("advantage:fur", "Fur", 1),
    PhysiologyBinding(
        "advantage:healing",
        "Healing",
        30,
        modifiers=(
            modifier("disease-only", -40),
            modifier("faith-healing", 20),
            modifier("injuries-only", -20),
            modifier("xenohealing", 20),
        ),
    ),
    PhysiologyBinding("advantage:sanitized-metabolism", "Sanitized Metabolism", 1),
    PhysiologyBinding("advantage:sealed", "Sealed", 15),
    PhysiologyBinding("advantage:metabolism-control", "Metabolism Control", 5, 10),
    PhysiologyBinding(
        "advantage:temperature-control",
        "Temperature Control",
        5,
        10,
        modifiers=(modifier("cold-only", -50), modifier("heat-only", -50)),
    ),
    PhysiologyBinding("advantage:unaging", "Unaging", 15),
    PhysiologyBinding("advantage:universal-digestion", "Universal Digestion", 5),
    PhysiologyBinding("advantage:pressure-support", "Pressure Support", 5, 3),
    PhysiologyBinding("advantage:vacuum-support", "Vacuum Support", 5),
    PhysiologyBinding(
        "advantage:radiation-tolerance",
        "Radiation Tolerance",
        5,
        parameters=(parameter("divisor", "integer", 2, 5, 10, 20, 50, 100, 200, 500, 1000),),
    ),
    PhysiologyBinding("advantage:recovery", "Recovery", 10),
    PhysiologyBinding(
        "advantage:regeneration",
        "Regeneration",
        25,
        parameters=(parameter("rate", "text", "regular", "fast", "very-fast", "extreme"),),
        modifiers=(modifier("radiation-only", -60),),
    ),
    PhysiologyBinding("advantage:regrowth", "Regrowth", 40),
    PhysiologyBinding(
        "disadvantage:bestial",
        "Bestial",
        -10,
        parameters=(parameter("speech", "boolean", False, True),),
    ),
    PhysiologyBinding("disadvantage:increased-life-support", "Increased Life Support", -10, 10),
    PhysiologyBinding(
        "disadvantage:cold-blooded",
        "Cold-Blooded",
        -5,
        parameters=(parameter("temperature", "text", "below-50f", "below-65f"),),
    ),
    PhysiologyBinding(
        "disadvantage:dependency",
        "Dependency",
        -5,
        parameters=(
            parameter("rarity", "text", "very-common", "common", "occasional", "rare"),
            parameter("interval", "text", "minute", "hour", "day", "week"),
        ),
    ),
    PhysiologyBinding("disadvantage:electrical", "Electrical", -20),
    PhysiologyBinding("disadvantage:short-lifespan", "Short Lifespan", -10, 4),
    PhysiologyBinding(
        "disadvantage:sleepy",
        "Sleepy",
        -5,
        parameters=(parameter("fraction", "text", "half", "three-quarters", "most"),),
    ),
    PhysiologyBinding("disadvantage:slow-eater", "Slow Eater", -10),
    PhysiologyBinding("disadvantage:nocturnal", "Nocturnal", -20),
    PhysiologyBinding(
        "disadvantage:stress-atavism",
        "Stress Atavism",
        -10,
        parameters=(parameter("severity", "text", "mild", "moderate", "severe"),),
    ),
    PhysiologyBinding(
        "disadvantage:unhealing",
        "Unhealing",
        -20,
        parameters=(parameter("kind", "text", "partial", "total"),),
    ),
    PhysiologyBinding("disadvantage:reprogrammable", "Reprogrammable", -10),
    PhysiologyBinding("disadvantage:unusual-biochemistry", "Unusual Biochemistry", -5),
    PhysiologyBinding(
        "disadvantage:weakness",
        "Weakness",
        -10,
        parameters=(
            parameter("rarity", "text", "very-common", "common", "occasional", "rare"),
            parameter("interval", "text", "minute", "five-minutes", "thirty-minutes"),
        ),
    ),
    PhysiologyBinding("disadvantage:self-destruct", "Self-Destruct", -10),
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


def metadata(binding: PhysiologyBinding) -> TraitRules:
    return TraitRules(
        PROFILE,
        binding.maximum_level,
        parameters=binding.parameters,
        modifiers=binding.modifiers,
        runtime_hooks=(binding.hook,),
    )


def definition(binding: PhysiologyBinding) -> RuleDefinition:
    return RuleDefinition(
        binding.id,
        DefinitionKind.TRAIT,
        binding.name,
        SOURCE_ID,
        binding.point_cost,
        ImplementationStatus.IMPLEMENTED,
        parameters=tuple(value.name for value in binding.parameters),
        hooks=("supernatural", "physiology-trait"),
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
                "B41-160",
            ),
        ),
        tuple(definition(binding) for binding in BINDINGS),
    )


def purchase_cost(binding: PhysiologyBinding, levels: int, options: TraitOptions) -> int:
    ordinary = cost(binding.point_cost, levels, options, metadata(binding))
    values = dict(options.parameters)
    if binding.id == "advantage:radiation-tolerance":
        base = {2: 5, 5: 10, 10: 15, 20: 20, 50: 25, 100: 30, 200: 35, 500: 40, 1000: 45}[
            int(values["divisor"])
        ]
    elif binding.id == "advantage:regeneration":
        base = {"regular": 25, "fast": 50, "very-fast": 100, "extreme": 150}[str(values["rate"])]
    elif binding.id == "disadvantage:bestial":
        base = -10 if values["speech"] else -15
    elif binding.id == "disadvantage:cold-blooded":
        base = -5 if values["temperature"] == "below-50f" else -10
    elif binding.id == "disadvantage:dependency":
        rarity = {"very-common": -5, "common": -10, "occasional": -20, "rare": -30}[
            str(values["rarity"])
        ]
        base = rarity * {"minute": 5, "hour": 4, "day": 3, "week": 1}[str(values["interval"])]
    elif binding.id == "disadvantage:sleepy":
        base = {"half": -5, "three-quarters": -10, "most": -20}[str(values["fraction"])]
    elif binding.id == "disadvantage:stress-atavism":
        base = {"mild": -5, "moderate": -10, "severe": -20}[str(values["severity"])]
    elif binding.id == "disadvantage:unhealing":
        base = -20 if values["kind"] == "partial" else -30
    elif binding.id == "disadvantage:weakness":
        rarity = {"very-common": -20, "common": -15, "occasional": -10, "rare": -5}[
            str(values["rarity"])
        ]
        base = (
            rarity * {"minute": 3, "five-minutes": 2, "thirty-minutes": 1}[str(values["interval"])]
        )
    else:
        return ordinary
    percent = max(
        -80,
        sum(selected.percent for selected in binding.modifiers if selected.id in options.modifiers),
    )
    return int(
        (Decimal(base) * Decimal(100 + percent) / 100).to_integral_value(rounding=ROUND_CEILING)
    )


def validate_purchase(entry: RuleDefinition, levels: int, options: TraitOptions) -> int:
    binding = BINDING_BY_ID.get(entry.id)
    if (
        binding is None
        or entry.trait_rules != metadata(binding)
        or binding.hook not in entry.trait_rules.runtime_hooks
    ):
        raise ValidationError("Physiology construction differs from its runtime binding")
    return purchase_cost(binding, levels, options)
