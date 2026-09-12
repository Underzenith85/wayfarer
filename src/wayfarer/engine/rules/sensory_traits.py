"""Pinned Basic Set senses and communication trait construction (B41-96)."""

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
from wayfarer.engine.rules.traits import (
    TraitModifier,
    TraitOptions,
    TraitParameter,
    TraitRules,
    cost,
)
from wayfarer.errors import ValidationError

PROFILE: Final = "gurps-basic-set-4e-2004"
SOURCE_ID: Final = "sjg:basic-set-characters-4e-2004"
PACKAGE_ID: Final = "package:gurps-basic-sensory-traits"


@dataclass(frozen=True, slots=True)
class SensoryBinding:
    id: str
    name: str
    point_cost: int
    maximum_level: int = 1
    parameters: tuple[TraitParameter, ...] = ()
    modifiers: tuple[TraitModifier, ...] = ()

    @property
    def hook(self) -> str:
        return "sensory-trait:" + self.id.split(":", 1)[1]


def parameter(
    name: str, kind: Literal["text", "integer", "boolean"], *choices: str | int | bool
) -> TraitParameter:
    return TraitParameter(name, kind, choices)


def modifier(identifier: str, percent: int, *exclusions: str) -> TraitModifier:
    return TraitModifier(identifier, percent, exclusions, "sensory-trait:" + identifier)


ZERO_OR_FULL = (parameter("native", "boolean", False, True),)
SENSE = parameter("sense", "text", "hearing", "smell", "taste", "touch", "vision")

BINDINGS: Final = (
    SensoryBinding(
        "advantage:detect",
        "Detect",
        10,
        parameters=(parameter("rarity", "text", "very-common", "common", "occasional", "rare"),),
        modifiers=(
            modifier("analyzing", 100),
            modifier("precise", 100),
            modifier("signal-detection", 0),
            modifier("vague", -50),
        ),
    ),
    SensoryBinding("advantage:digital-mind", "Digital Mind", 5),
    SensoryBinding(
        "advantage:discriminatory-hearing",
        "Discriminatory Hearing",
        15,
        modifiers=(modifier("emotion-sense", 50),),
    ),
    SensoryBinding(
        "advantage:discriminatory-smell",
        "Discriminatory Smell",
        15,
        modifiers=(modifier("emotion-sense", 50),),
    ),
    SensoryBinding("advantage:discriminatory-taste", "Discriminatory Taste", 10),
    SensoryBinding(
        "advantage:chameleon",
        "Chameleon",
        5,
        100,
        modifiers=(
            modifier("extended-infravision", 20),
            modifier("extended-ultravision", 20),
            modifier("always-on", -10),
        ),
    ),
    SensoryBinding(
        "advantage:clairsentience",
        "Clairsentience",
        50,
        modifiers=(
            modifier("clairaudience", -30),
            modifier("clairvoyance", -10),
            modifier("increased-range-1", 10),
            modifier("world-spanning", 100),
        ),
    ),
    SensoryBinding("advantage:hyperspectral-vision", "Hyperspectral Vision", 25),
    SensoryBinding(
        "advantage:dark-vision", "Dark Vision", 25, modifiers=(modifier("color-vision", 20),)
    ),
    SensoryBinding("advantage:infravision", "Infravision", 10, parameters=ZERO_OR_FULL),
    SensoryBinding(
        "advantage:scanning-sense",
        "Scanning Sense",
        20,
        parameters=(parameter("kind", "text", "imaging-radar", "radar", "sonar", "para-radar"),),
        modifiers=(
            modifier("extended-arc", 50),
            modifier("low-probability-intercept", 20),
            modifier("multi-mode", 50),
            modifier("targeting", 20),
        ),
    ),
    SensoryBinding(
        "advantage:see-invisible",
        "See Invisible",
        15,
        parameters=(parameter("category", "text", "magic", "psi", "spirit", "technology"),),
    ),
    SensoryBinding(
        "advantage:invisibility",
        "Invisibility",
        40,
        modifiers=(
            modifier("affects-machines", 50),
            modifier("can-carry-objects", 10),
            modifier("switchable", 10),
            modifier("usually-on", 5),
        ),
    ),
    SensoryBinding("advantage:sensitive-touch", "Sensitive Touch", 10),
    SensoryBinding("advantage:silence", "Silence", 5, 100),
    SensoryBinding("advantage:speak-underwater", "Speak Underwater", 5),
    SensoryBinding(
        "advantage:speak-with-animals",
        "Speak With Animals",
        25,
        parameters=(parameter("scope", "text", "all", "broad", "class", "species"),),
    ),
    SensoryBinding("advantage:speak-with-plants", "Speak With Plants", 15),
    SensoryBinding("advantage:microscopic-vision", "Microscopic Vision", 5, 100),
    SensoryBinding(
        "advantage:mimicry",
        "Mimicry",
        10,
        parameters=(parameter("kind", "text", "speech", "bird-calls", "animal-sounds"),),
    ),
    SensoryBinding("advantage:subsonic-hearing", "Subsonic Hearing", 5, parameters=ZERO_OR_FULL),
    SensoryBinding("advantage:subsonic-speech", "Subsonic Speech", 10, parameters=ZERO_OR_FULL),
    SensoryBinding(
        "advantage:obscure",
        "Obscure",
        2,
        10,
        (SENSE,),
        (modifier("defensive", 50), modifier("extended", 20), modifier("stealthy", 100)),
    ),
    SensoryBinding(
        "advantage:telecommunication",
        "Telecommunication",
        10,
        parameters=(parameter("kind", "text", "infrared", "laser", "radio", "telesend"),),
        modifiers=(
            modifier("broadcast", 50),
            modifier("receive-only", -50),
            modifier("secure", 20),
            modifier("video", 40),
        ),
    ),
    SensoryBinding("advantage:telescopic-vision", "Telescopic Vision", 5, 100),
    SensoryBinding("advantage:parabolic-hearing", "Parabolic Hearing", 4, 100),
    SensoryBinding(
        "advantage:penetrating-vision",
        "Penetrating Vision",
        10,
        100,
        modifiers=(modifier("specific-material", -40),),
    ),
    SensoryBinding("advantage:ultrahearing", "Ultrahearing", 5, parameters=ZERO_OR_FULL),
    SensoryBinding("advantage:ultrasonic-speech", "Ultrasonic Speech", 10, parameters=ZERO_OR_FULL),
    SensoryBinding("advantage:ultravision", "Ultravision", 10, parameters=ZERO_OR_FULL),
    SensoryBinding("advantage:protected-sense", "Protected Sense", 5, parameters=(SENSE,)),
    SensoryBinding(
        "advantage:vibration-sense",
        "Vibration Sense",
        10,
        parameters=(parameter("medium", "text", "air", "earth", "water"),),
        modifiers=(modifier("universal", 50),),
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


def metadata(binding: SensoryBinding) -> TraitRules:
    return TraitRules(
        PROFILE,
        binding.maximum_level,
        parameters=binding.parameters,
        modifiers=binding.modifiers,
        runtime_hooks=(binding.hook,),
    )


def definition(binding: SensoryBinding) -> RuleDefinition:
    return RuleDefinition(
        binding.id,
        DefinitionKind.TRAIT,
        binding.name,
        SOURCE_ID,
        binding.point_cost,
        ImplementationStatus.IMPLEMENTED,
        parameters=tuple(value.name for value in binding.parameters),
        hooks=("supernatural", "sensory-trait"),
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
                "B41-96",
            ),
        ),
        tuple(definition(binding) for binding in BINDINGS),
    )


def purchase_cost(binding: SensoryBinding, levels: int, options: TraitOptions) -> int:
    ordinary = cost(binding.point_cost, levels, options, metadata(binding))
    values = dict(options.parameters)
    if binding.id == "advantage:detect":
        base = {"rare": 5, "occasional": 10, "common": 20, "very-common": 30}[str(values["rarity"])]
    elif binding.id == "advantage:scanning-sense":
        base = {"imaging-radar": 20, "radar": 20, "sonar": 20, "para-radar": 40}[
            str(values["kind"])
        ]
    elif binding.id == "advantage:telecommunication":
        base = {"infrared": 10, "laser": 15, "radio": 10, "telesend": 30}[str(values["kind"])]
    elif binding.parameters == ZERO_OR_FULL:
        base = 0 if values["native"] else binding.point_cost
    elif binding.id == "advantage:speak-with-animals":
        base = {"all": 25, "broad": 20, "class": 15, "species": 10}[str(values["scope"])]
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
        raise ValidationError("Sensory construction differs from its runtime binding")
    return purchase_cost(binding, levels, options)
