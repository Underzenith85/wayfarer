"""Pinned Basic Set attack and defense trait construction (Characters B35-161)."""

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
PACKAGE_ID: Final = "package:gurps-basic-attack-defense-traits"


@dataclass(frozen=True, slots=True)
class AttackDefenseBinding:
    id: str
    name: str
    point_cost: int
    maximum_level: int = 1
    parameters: tuple[TraitParameter, ...] = ()
    modifiers: tuple[TraitModifier, ...] = ()

    @property
    def hook(self) -> str:
        return "attack-defense-trait:" + self.id.split(":", 1)[1]


def parameter(
    name: str, kind: Literal["text", "integer", "boolean"], *choices: str | int | bool
) -> TraitParameter:
    return TraitParameter(name, kind, choices)


def modifier(identifier: str, percent: int, *exclusions: str) -> TraitModifier:
    return TraitModifier(identifier, percent, exclusions, "attack-defense-trait:" + identifier)


BINDINGS: Final = (
    AttackDefenseBinding(
        "advantage:affliction",
        "Affliction",
        10,
        100,
        parameters=(parameter("effect", "text", "attribute-penalty", "incapacitation", "stun"),),
        modifiers=(modifier("malediction", 100), modifier("secondary", 20)),
    ),
    AttackDefenseBinding(
        "advantage:binding",
        "Binding",
        2,
        100,
        modifiers=(
            modifier("constricting", 75),
            modifier("engulfing", 60),
            modifier("only-damaged-by", 30),
            modifier("sticky", 20),
            modifier("unbreakable", 40),
        ),
    ),
    AttackDefenseBinding(
        "advantage:claws",
        "Claws",
        3,
        parameters=(parameter("kind", "text", "blunt", "sharp", "talons", "long-talons"),),
    ),
    AttackDefenseBinding("advantage:constriction-attack", "Constriction Attack", 15),
    AttackDefenseBinding(
        "advantage:damage-resistance",
        "Damage Resistance",
        5,
        1000,
        modifiers=(
            modifier("absorption", 80),
            modifier("force-field", 20),
            modifier("hardened", 20),
            modifier("flexible", -20),
            modifier("partial", -10),
        ),
    ),
    AttackDefenseBinding(
        "advantage:injury-tolerance",
        "Injury Tolerance",
        20,
        parameters=(parameter("kind", "text", "diffuse", "homogeneous", "unliving"),),
    ),
    AttackDefenseBinding(
        "advantage:innate-attack",
        "Innate Attack",
        5,
        100,
        parameters=(
            parameter(
                "damage-type",
                "text",
                "burn",
                "cor",
                "cr",
                "cut",
                "imp",
                "pi-",
                "pi",
                "pi+",
                "pi++",
                "tox",
            ),
        ),
        modifiers=(modifier("armor-divisor", 50), modifier("melee", -10)),
    ),
    AttackDefenseBinding("advantage:nictitating-membrane", "Nictitating Membrane", 1, 100),
    AttackDefenseBinding(
        "advantage:spines",
        "Spines",
        1,
        parameters=(parameter("kind", "text", "short", "long"),),
    ),
    AttackDefenseBinding(
        "advantage:striker",
        "Striker",
        5,
        parameters=(parameter("damage-type", "text", "cr", "cut", "imp", "pi"),),
        modifiers=(modifier("long", 100), modifier("weak", -50)),
    ),
    AttackDefenseBinding("advantage:striking-st", "Striking ST", 5, 100),
    AttackDefenseBinding("advantage:supernatural-durability", "Supernatural Durability", 150),
    AttackDefenseBinding(
        "advantage:teeth",
        "Teeth",
        0,
        parameters=(parameter("kind", "text", "blunt", "sharp", "fangs"),),
    ),
    AttackDefenseBinding("advantage:unkillable", "Unkillable", 50, 3),
    AttackDefenseBinding("advantage:vampiric-bite", "Vampiric Bite", 30),
    AttackDefenseBinding(
        "disadvantage:fragile",
        "Fragile",
        -5,
        parameters=(
            parameter("kind", "text", "combustible", "explosive", "flammable", "unnatural"),
        ),
    ),
    AttackDefenseBinding(
        "disadvantage:vulnerability",
        "Vulnerability",
        -10,
        parameters=(
            parameter("rarity", "text", "very-common", "common", "occasional", "rare"),
            parameter("multiplier", "integer", 2, 3, 4),
        ),
    ),
    AttackDefenseBinding("disadvantage:weak-bite", "Weak Bite", -2),
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


def metadata(binding: AttackDefenseBinding) -> TraitRules:
    return TraitRules(
        PROFILE,
        binding.maximum_level,
        parameters=binding.parameters,
        modifiers=binding.modifiers,
        runtime_hooks=(binding.hook,),
    )


def definition(binding: AttackDefenseBinding) -> RuleDefinition:
    return RuleDefinition(
        binding.id,
        DefinitionKind.TRAIT,
        binding.name,
        SOURCE_ID,
        binding.point_cost,
        ImplementationStatus.IMPLEMENTED,
        parameters=tuple(value.name for value in binding.parameters),
        hooks=("supernatural", "attack-defense-trait"),
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
                "B35-161",
            ),
        ),
        tuple(definition(binding) for binding in BINDINGS),
    )


def purchase_cost(binding: AttackDefenseBinding, levels: int, options: TraitOptions) -> int:
    ordinary = cost(binding.point_cost, levels, options, metadata(binding))
    values = dict(options.parameters)
    if binding.id == "advantage:claws":
        base = {"blunt": 3, "sharp": 5, "talons": 8, "long-talons": 11}[str(values["kind"])]
    elif binding.id == "advantage:injury-tolerance":
        base = {"diffuse": 100, "homogeneous": 40, "unliving": 20}[str(values["kind"])]
    elif binding.id == "advantage:innate-attack":
        base = {
            "burn": 5,
            "cor": 10,
            "cr": 5,
            "cut": 7,
            "imp": 8,
            "pi-": 3,
            "pi": 5,
            "pi+": 6,
            "pi++": 8,
            "tox": 4,
        }[str(values["damage-type"])] * levels
    elif binding.id == "advantage:spines":
        base = 1 if values["kind"] == "short" else 3
    elif binding.id == "advantage:striker":
        base = {"cr": 5, "cut": 7, "imp": 8, "pi": 5}[str(values["damage-type"])]
    elif binding.id == "advantage:teeth":
        return {"blunt": 0, "sharp": 1, "fangs": 2}[str(values["kind"])]
    elif binding.id == "disadvantage:fragile":
        return {"combustible": -5, "explosive": -15, "flammable": -10, "unnatural": -50}[
            str(values["kind"])
        ]
    elif binding.id == "disadvantage:vulnerability":
        rarity = {"rare": 10, "occasional": 20, "common": 30, "very-common": 40}[
            str(values["rarity"])
        ]
        factor = {2: Decimal(1), 3: Decimal("1.5"), 4: Decimal(2)}[int(values["multiplier"])]
        return -int(Decimal(rarity) * factor)
    else:
        return ordinary
    percent = max(-80, sum(m.percent for m in binding.modifiers if m.id in options.modifiers))
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
        raise ValidationError("Attack/defense construction differs from its runtime binding")
    return purchase_cost(binding, levels, options)
