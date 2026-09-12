"""Pinned Basic Set mental and spirit trait construction (Characters B40-161)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
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
PACKAGE_ID: Final = "package:gurps-basic-mental-spirit-traits"


@dataclass(frozen=True, slots=True)
class MentalSpiritBinding:
    id: str
    name: str
    point_cost: int
    maximum_level: int = 1
    parameters: tuple[TraitParameter, ...] = ()
    modifiers: tuple[TraitModifier, ...] = ()

    @property
    def hook(self) -> str:
        return "mental-spirit-trait:" + self.id.split(":", 1)[1]


def parameter(
    name: str, kind: Literal["text", "integer", "boolean"], *choices: str | int | bool
) -> TraitParameter:
    return TraitParameter(name, kind, choices)


def modifier(identifier: str, percent: int, *exclusions: str) -> TraitModifier:
    return TraitModifier(identifier, percent, exclusions, "mental-spirit-trait:" + identifier)


RARITY = parameter("rarity", "text", "very-common", "common", "occasional", "rare")
SELF_CONTROL = parameter("self-control", "integer", 6, 9, 12, 15)

BINDINGS: Final = (
    MentalSpiritBinding(
        "advantage:blessed",
        "Blessed",
        10,
        parameters=(parameter("kind", "text", "blessed", "heroic-feats", "very-blessed"),),
    ),
    MentalSpiritBinding("advantage:channeling", "Channeling", 10),
    MentalSpiritBinding("advantage:compartmentalized-mind", "Compartmentalized Mind", 50, 100),
    MentalSpiritBinding("advantage:destiny", "Destiny", 5, 3),
    MentalSpiritBinding(
        "advantage:dominance",
        "Dominance",
        20,
        modifiers=(modifier("infectious", 100), modifier("no-control", -40)),
    ),
    MentalSpiritBinding("advantage:higher-purpose", "Higher Purpose", 5, 100),
    MentalSpiritBinding("advantage:illuminated", "Illuminated", 15),
    MentalSpiritBinding("advantage:medium", "Medium", 10, modifiers=(modifier("visual", 40),)),
    MentalSpiritBinding(
        "advantage:mind-control",
        "Mind Control",
        50,
        modifiers=(
            modifier("conditioning", 50, "conditioning-only"),
            modifier("conditioning-only", -50, "conditioning"),
            modifier("cybernetic", -50),
            modifier("independent", 70),
            modifier("puppet", -40),
        ),
    ),
    MentalSpiritBinding(
        "advantage:mind-probe",
        "Mind Probe",
        20,
        modifiers=(modifier("invasive", 75), modifier("universal", 50)),
    ),
    MentalSpiritBinding(
        "advantage:mind-reading",
        "Mind Reading",
        30,
        modifiers=(
            modifier("multiple-contacts", 50),
            modifier("sensory", 20),
            modifier("universal", 50),
        ),
    ),
    MentalSpiritBinding("advantage:mind-shield", "Mind Shield", 4, 100),
    MentalSpiritBinding(
        "advantage:mindlink",
        "Mindlink",
        5,
        parameters=(parameter("group-size", "integer", 1, 9, 99, 999),),
    ),
    MentalSpiritBinding(
        "advantage:modular-abilities",
        "Modular Abilities",
        10,
        parameters=(
            parameter("framework", "text", "chip-slots", "cosmic-power", "super-memorization"),
            parameter("capacity", "integer", 1, 2, 5, 10, 20),
            parameter("slots", "integer", 1, 2, 3, 4),
        ),
        modifiers=(
            modifier("physical", 50),
            modifier("social", 50),
        ),
    ),
    MentalSpiritBinding(
        "advantage:neutralize",
        "Neutralize",
        50,
        modifiers=(modifier("power-theft", 200), modifier("one-ability", -80)),
    ),
    MentalSpiritBinding("advantage:oracle", "Oracle", 15),
    MentalSpiritBinding(
        "advantage:possession",
        "Possession",
        100,
        modifiers=(
            modifier("chronic", 20),
            modifier("digital", -40),
            modifier("mind-swap", 10),
            modifier("parasitic", -60),
            modifier("spiritual", -20),
            modifier("telecontrol", 50),
        ),
    ),
    MentalSpiritBinding("advantage:precognition", "Precognition", 25),
    MentalSpiritBinding(
        "advantage:psi-static", "Psi Static", 30, modifiers=(modifier("switchable", 100),)
    ),
    MentalSpiritBinding("advantage:psychometry", "Psychometry", 20),
    MentalSpiritBinding("advantage:puppet", "Puppet", 5, 100),
    MentalSpiritBinding(
        "advantage:racial-memory",
        "Racial Memory",
        15,
        parameters=(parameter("active", "boolean", False, True),),
    ),
    MentalSpiritBinding("advantage:reawakened", "Reawakened", 10),
    MentalSpiritBinding("advantage:special-rapport", "Special Rapport", 5),
    MentalSpiritBinding("advantage:spirit-empathy", "Spirit Empathy", 10),
    MentalSpiritBinding("advantage:super-luck", "Super Luck", 100),
    MentalSpiritBinding("advantage:temporal-inertia", "Temporal Inertia", 15),
    MentalSpiritBinding(
        "advantage:terror",
        "Terror",
        30,
        parameters=(parameter("penalty", "integer", 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10),),
        modifiers=(modifier("always-on", -20), modifier("presence", -20)),
    ),
    MentalSpiritBinding(
        "advantage:true-faith", "True Faith", 15, modifiers=(modifier("turning", 65),)
    ),
    MentalSpiritBinding("advantage:visualization", "Visualization", 10),
    MentalSpiritBinding(
        "advantage:wild-talent", "Wild Talent", 20, 100, modifiers=(modifier("retention", 25),)
    ),
    MentalSpiritBinding("disadvantage:cursed", "Cursed", -75),
    MentalSpiritBinding("disadvantage:destiny", "Destiny", -5, 3),
    MentalSpiritBinding(
        "disadvantage:divine-curse",
        "Divine Curse",
        -5,
        parameters=(parameter("value", "integer", 5, 10, 15, 20, 30, 50, 75),),
    ),
    MentalSpiritBinding("disadvantage:draining", "Draining", -5),
    MentalSpiritBinding("disadvantage:dread", "Dread", -10, parameters=(RARITY,)),
    MentalSpiritBinding("disadvantage:frightens-animals", "Frightens Animals", -10),
    MentalSpiritBinding("disadvantage:infectious-attack", "Infectious Attack", -5),
    MentalSpiritBinding("disadvantage:lifebane", "Lifebane", -10),
    MentalSpiritBinding("disadvantage:revulsion", "Revulsion", -5, parameters=(RARITY,)),
    MentalSpiritBinding(
        "disadvantage:supernatural-features",
        "Supernatural Features",
        -1,
        parameters=(
            parameter(
                "feature",
                "text",
                "no-body-heat",
                "no-reflection",
                "no-shadow",
                "pallor",
            ),
        ),
    ),
    MentalSpiritBinding("disadvantage:supersensitive", "Supersensitive", -15),
    MentalSpiritBinding(
        "disadvantage:uncontrollable-appetite",
        "Uncontrollable Appetite",
        -15,
        parameters=(SELF_CONTROL,),
    ),
    MentalSpiritBinding("disadvantage:unique", "Unique", -10),
    MentalSpiritBinding("disadvantage:weirdness-magnet", "Weirdness Magnet", -15),
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


def metadata(binding: MentalSpiritBinding) -> TraitRules:
    return TraitRules(
        PROFILE,
        binding.maximum_level,
        parameters=binding.parameters,
        modifiers=binding.modifiers,
        runtime_hooks=(binding.hook,),
    )


def definition(binding: MentalSpiritBinding) -> RuleDefinition:
    return RuleDefinition(
        binding.id,
        DefinitionKind.TRAIT,
        binding.name,
        SOURCE_ID,
        binding.point_cost,
        ImplementationStatus.IMPLEMENTED,
        parameters=tuple(value.name for value in binding.parameters),
        hooks=("supernatural", "mental-spirit-trait"),
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
                "B40-161",
            ),
        ),
        tuple(definition(binding) for binding in BINDINGS),
    )


def purchase_cost(binding: MentalSpiritBinding, levels: int, options: TraitOptions) -> int:
    ordinary = cost(binding.point_cost, levels, options, metadata(binding))
    values = dict(options.parameters)
    if binding.id == "advantage:blessed":
        base = 20 if values["kind"] == "very-blessed" else 10
    elif binding.id == "advantage:mindlink":
        base = {1: 5, 9: 10, 99: 20, 999: 30}[int(values["group-size"])]
    elif binding.id == "advantage:modular-abilities":
        capacity, slots = int(values["capacity"]), int(values["slots"])
        base = 10 * capacity if values["framework"] == "cosmic-power" else 5 * slots + 3 * capacity
    elif binding.id == "advantage:racial-memory":
        base = 40 if values["active"] else 15
    elif binding.id == "advantage:terror":
        base = 30 + 10 * int(values["penalty"])
    elif binding.id == "disadvantage:divine-curse":
        return -int(values["value"])
    elif binding.id in {"disadvantage:dread", "disadvantage:revulsion"}:
        base = {"very-common": -20, "common": -15, "occasional": -10, "rare": -5}[
            str(values["rarity"])
        ]
    elif binding.id == "disadvantage:supernatural-features":
        base = {"no-body-heat": -5, "no-reflection": -10, "no-shadow": -10, "pallor": -10}[
            str(values["feature"])
        ]
    elif binding.id == "disadvantage:uncontrollable-appetite":
        base = {6: -30, 9: -22, 12: -15, 15: -7}[int(values["self-control"])]
    else:
        return ordinary
    percent = max(
        -80,
        sum(selected.percent for selected in binding.modifiers if selected.id in options.modifiers),
    )
    value = Decimal(base) * Decimal(100 + percent) / 100
    rounding = ROUND_CEILING
    return int(value.to_integral_value(rounding=rounding))


def validate_purchase(entry: RuleDefinition, levels: int, options: TraitOptions) -> int:
    binding = BINDING_BY_ID.get(entry.id)
    if (
        binding is None
        or entry.trait_rules != metadata(binding)
        or binding.hook not in entry.trait_rules.runtime_hooks
    ):
        raise ValidationError("Mental/spirit construction differs from its runtime binding")
    return purchase_cost(binding, levels, options)
