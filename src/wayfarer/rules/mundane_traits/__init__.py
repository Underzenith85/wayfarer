"""Selected mundane traits and backgrounds, separate from frozen campaign pins.

Numeric constructions: Basic Set Characters, Fourth Edition, third printing.
The first-printing/2007-01-26 errata delta remains an explicit audit blocker.
Construction cost and executable effect are separate: an entry is implemented
only when `runtime.SUPPORTED_HOOKS` binds its effect to a service that already
resolves it. Every other record stays unsupported and cannot activate.
"""

from collections import Counter
from dataclasses import asdict, dataclass, replace
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
    SourceReference,
)
from wayfarer.rules.mundane_traits.runtime import (
    APPEARANCE_BINDINGS,
    REACTION_BINDINGS,
    REPUTATION_BINDINGS,
    SUPPORTED_HOOKS,
)
from wayfarer.rules.physical_traits import PHYSICAL_BINDINGS, PHYSICAL_HOOKS
from wayfarer.rules.traits import TraitRules, validate_metadata

PROFILE: Final = "gurps-basic-set-4e-2004"
SOURCE = SourceReference(
    "sjg:basic-set-characters-4e-third-printing-candidates",
    "Basic Set: Characters, Fourth Edition, third printing",
    "user-supplied-reference",
    "Numeric construction metadata only; frozen first-printing delta pending",
)
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=60)]


class Vocabulary(Record):
    """Trusted campaign identifiers; each expansion is bound by the package digest.

    Languages here are additional languages, not the native language granted for
    free. Native-language reductions need separate adjudication and are excluded.
    Names never choose a cost, effect, or arbitrary executable expression.
    """

    languages: tuple[Identifier, ...] = Field(default=("trade",), strict=False, max_length=30)
    cultures: tuple[Identifier, ...] = Field(default=("foreign",), strict=False, max_length=30)
    organizations: tuple[Identifier, ...] = Field(default=("watch",), strict=False, max_length=30)
    people: tuple[Identifier, ...] = Field(default=("associate",), strict=False, max_length=30)

    @model_validator(mode="after")
    def unique(self) -> Self:
        for values in (self.languages, self.cultures, self.organizations, self.people):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate background identity")
        return self


@dataclass(frozen=True, slots=True)
class TraitEntry:
    id: str
    name: str
    points: int
    page: int
    category: Literal["advantage", "disadvantage", "perk", "quirk", "background"]
    effect: str
    maximum_level: int = 1
    self_control: bool = False
    exclusion_group: str | None = None
    obligations: tuple[str, ...] = ()
    identity: str | None = None
    prerequisites: tuple[str, ...] = ()
    followup_issues: tuple[int, ...] = (113, 122)

    @property
    def implemented(self) -> bool:
        """A bound effect executes; naming an effect never implements it."""
        return self.effect in SUPPORTED_HOOKS

    @property
    def status(self) -> ImplementationStatus:
        return (
            ImplementationStatus.IMPLEMENTED
            if self.implemented
            else ImplementationStatus.UNSUPPORTED
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        binding = REACTION_BINDINGS.get(self.id)
        return (
            "first-printing-delta-audit",
            *(() if self.implemented else (self.effect,)),
            *(binding.blockers if binding is not None else ()),
            *(("disadvantage-consequences",) if self.effect == "trait.self_control" else ()),
        )

    @property
    def reference(self) -> str:
        return f"B{self.page}"

    def definition(self, entries: tuple[TraitEntry, ...]) -> RuleDefinition:
        return RuleDefinition(
            self.id,
            DefinitionKind.TRAIT,
            self.name,
            SOURCE.id,
            self.points,
            self.status,
            prerequisites=self.prerequisites,
            exclusions=tuple(
                e.id
                for e in entries
                if e.id != self.id
                and (
                    (self.exclusion_group is not None and e.exclusion_group == self.exclusion_group)
                    or ("rank-replaces-status-" in self.id and e.effect == "trait.status")
                    or ("rank-replaces-status-" in e.id and self.effect == "trait.status")
                )
            ),
            hooks=tuple(f"manual-obligation:{key}" for key in self.obligations),
            trait_rules=TraitRules(
                PROFILE,
                maximum_level=self.maximum_level,
                self_control=self.self_control,
                runtime_hooks=(self.effect,),
            ),
        )


def _entry(
    key: str,
    name: str,
    points: int,
    page: int,
    effect: str,
    *,
    levels: int = 1,
    control: bool = False,
    group: str | None = None,
    obligations: tuple[str, ...] = (),
    category: Literal["advantage", "disadvantage", "perk", "quirk", "background"] | None = None,
    identity: str | None = None,
) -> TraitEntry:
    return TraitEntry(
        f"trait:{key}",
        name,
        points,
        page,
        category or ("disadvantage" if points < 0 else "advantage"),
        effect,
        levels,
        control,
        group,
        obligations,
        identity,
    )


DEFAULT_VOCABULARY = Vocabulary()

# Concrete runtime owners, distinct from the source-reconciliation owner #191.
EFFECT_OWNERS: Final = {
    **dict.fromkeys(
        (
            "trait.off_hand",
            "trait.combat_reflexes",
            "trait.fitness",
            "trait.pain",
            "trait.darkness",
            "trait.healing",
            "trait.senses",
        ),
        332,
    ),
    **dict.fromkeys(
        (
            "trait.memory",
            "trait.concentration",
            "trait.creativity",
            "trait.shyness",
            "trait.penetrating_voice",
            "trait.manual_obligation",
            "trait.honesty",
            "trait.truthfulness",
            "trait.self_control",
            "trait.associated_npc",
            "trait.contact",
        ),
        333,
    ),
    **dict.fromkeys(
        (
            "trait.wealth",
            "trait.status",
            "trait.rank",
            "trait.language",
            "trait.language_talent",
            "trait.culture",
        ),
        334,
    ),
    "trait.voice": 335,
    "trait.appearance_resentment": 335,
}


def inventory(vocabulary: Vocabulary = DEFAULT_VOCABULARY) -> tuple[TraitEntry, ...]:
    vocabulary = Vocabulary.model_validate(vocabulary)
    entries = [
        _entry("ambidexterity", "Ambidexterity", 5, 39, "trait.off_hand"),
        _entry("charisma", "Charisma", 5, 41, "trait.social_modifiers", levels=10),
        _entry("combat-reflexes", "Combat Reflexes", 15, 43, "trait.combat_reflexes"),
        _entry("eidetic-memory", "Eidetic Memory", 5, 51, "trait.memory", group="memory"),
        _entry(
            "photographic-memory", "Photographic Memory", 10, 51, "trait.memory", group="memory"
        ),
        _entry("fit", "Fit", 5, 55, "trait.fitness", group="fitness"),
        _entry("very-fit", "Very Fit", 15, 55, "trait.fitness", group="fitness"),
        _entry("high-pain-threshold", "High Pain Threshold", 10, 59, "trait.pain"),
        _entry("language-talent", "Language Talent", 10, 65, "trait.language_talent"),
        _entry("night-vision", "Night Vision", 1, 71, "trait.darkness", levels=9),
        _entry("rapid-healing", "Rapid Healing", 5, 79, "trait.healing", group="healing"),
        _entry(
            "very-rapid-healing", "Very Rapid Healing", 15, 79, "trait.healing", group="healing"
        ),
        _entry("single-minded", "Single-Minded", 5, 85, "trait.concentration"),
        _entry("versatile", "Versatile", 5, 96, "trait.creativity"),
        _entry("voice", "Voice", 10, 97, "trait.voice"),
        _entry("bad-temper", "Bad Temper", -10, 124, "trait.self_control", control=True),
        _entry("curious", "Curious", -5, 129, "trait.self_control", control=True),
        _entry("honesty", "Honesty", -10, 138, "trait.honesty", control=True),
        _entry("overconfidence", "Overconfidence", -5, 148, "trait.self_control", control=True),
        _entry("truthfulness", "Truthfulness", -5, 159, "trait.truthfulness", control=True),
        _entry(
            "status",
            "Status",
            5,
            28,
            "trait.status",
            levels=8,
            group="status",
            category="background",
        ),
        _entry(
            "low-status",
            "Low Status",
            -5,
            28,
            "trait.status",
            levels=2,
            group="status",
            category="background",
        ),
        _entry("shyness-mild", "Mild Shyness", -5, 154, "trait.shyness", group="shyness"),
        _entry("shyness-severe", "Severe Shyness", -10, 154, "trait.shyness", group="shyness"),
        _entry(
            "shyness-crippling", "Crippling Shyness", -20, 154, "trait.shyness", group="shyness"
        ),
        _entry(
            "perk-penetrating-voice",
            "Penetrating Voice",
            1,
            101,
            "trait.penetrating_voice",
            category="perk",
        ),
        _entry(
            "quirk-careful",
            "Careful",
            -1,
            163,
            "trait.manual_obligation",
            category="quirk",
            obligations=("extra-preparation-time-and-expense",),
        ),
        _entry(
            "code-of-honor-soldier",
            "Code of Honor (Soldier)",
            -10,
            127,
            "trait.manual_obligation",
            obligations=("soldier-code",),
        ),
    ]
    for level, points in (
        ("hideous", -16),
        ("ugly", -8),
        ("unattractive", -4),
        ("average", 0),
        ("attractive", 4),
        ("handsome", 12),
        ("very-handsome", 16),
    ):
        entries.append(
            _entry(
                f"appearance-{level}",
                f"Appearance ({level})",
                points,
                21,
                "trait.appearance" if level != "very-handsome" else "trait.appearance_resentment",
                group="appearance",
                category="background",
            )
        )
    for detail, points in (("bravery", 5), ("cruelty", -5)):
        entries.append(
            _entry(
                f"reputation-{detail}",
                f"Reputation ({detail}; everyone; always)",
                points,
                27,
                "trait.reputation",
                levels=4,
                category="background",
                identity=detail,
            )
        )
    for sense in ("hearing", "taste-smell", "touch", "vision"):
        entries.append(_entry(f"acute-{sense}", f"Acute {sense}", 2, 35, "trait.senses", levels=10))
    for key, points in (
        ("dead-broke", -25),
        ("poor", -15),
        ("struggling", -10),
        ("average", 0),
        ("comfortable", 10),
        ("wealthy", 20),
        ("very-wealthy", 30),
        ("filthy-rich", 50),
    ):
        entries.append(
            _entry(
                f"wealth-{key}",
                f"Wealth ({key})",
                points,
                25,
                "trait.wealth",
                group="wealth",
                category="background",
            )
        )
    for scope, points in (
        ("individual", -2),
        ("small-group", -5),
        ("large-group", -10),
        ("race", -15),
        ("all-living", -20),
    ):
        entries.append(
            _entry(
                f"sense-of-duty-{scope}",
                f"Sense of Duty ({scope})",
                points,
                153,
                "trait.manual_obligation",
                group="sense-of-duty",
                obligations=(f"protect-{scope}",),
            )
        )
    for language in sorted(vocabulary.languages):
        for mode in ("spoken", "written"):
            entries.append(
                _entry(
                    f"language-{language}-{mode}",
                    f"Language ({language}, {mode})",
                    1,
                    24,
                    "trait.language",
                    levels=3,
                    category="background",
                    identity=language,
                )
            )
    for culture in sorted(vocabulary.cultures):
        entries.append(
            _entry(
                f"culture-{culture}",
                f"Cultural Familiarity ({culture})",
                1,
                23,
                "trait.culture",
                category="background",
                identity=culture,
            )
        )
    for organization in sorted(vocabulary.organizations):
        for variant, points, page in (
            ("rank", 5, 29),
            ("rank-replaces-status", 10, 30),
            ("courtesy-rank", 1, 29),
        ):
            entries.append(
                _entry(
                    f"{variant}-{organization}",
                    f"{variant} ({organization})",
                    points,
                    page,
                    "trait.rank",
                    levels=8,
                    group=f"rank-{organization}",
                    category="background",
                    identity=organization,
                )
            )
    for person in sorted(vocabulary.people):
        entries.extend(
            (
                _entry(
                    f"ally-{person}",
                    f"Ally ({person}; 100%; 9 or less)",
                    5,
                    36,
                    "trait.associated_npc",
                    category="background",
                    identity=person,
                    obligations=("protect-ally",),
                ),
                _entry(
                    f"contact-{person}",
                    f"Contact ({person}; skill 12; usually reliable; 9 or less)",
                    2,
                    44,
                    "trait.contact",
                    category="background",
                    identity=person,
                ),
                _entry(
                    f"patron-{person}",
                    f"Patron ({person}; powerful individual; 9 or less)",
                    10,
                    72,
                    "trait.associated_npc",
                    category="background",
                    identity=person,
                ),
                _entry(
                    f"dependent-{person}",
                    f"Dependent ({person}; 50%; friend; 9 or less)",
                    -5,
                    131,
                    "trait.associated_npc",
                    category="background",
                    identity=person,
                    obligations=("protect-dependent",),
                ),
                _entry(
                    f"enemy-{person}",
                    f"Enemy ({person}; equal power; hunter; 9 or less)",
                    -10,
                    135,
                    "trait.associated_npc",
                    category="background",
                    identity=person,
                ),
            )
        )
    result = tuple(
        replace(
            e,
            followup_issues=(191, EFFECT_OWNERS[e.effect])
            if e.effect in EFFECT_OWNERS and e.effect not in PHYSICAL_HOOKS
            else (191,),
        )
        for e in entries
    )
    if not REACTION_BINDINGS.keys() <= {entry.id for entry in result}:
        raise ValidationError("Runtime binding without a selected trait record")
    validate_inventory(result)
    return result


def validate_inventory(entries: tuple[TraitEntry, ...]) -> None:
    identifiers = {entry.id for entry in entries}
    if len(identifiers) != len(entries):
        raise ValidationError("Duplicate mundane trait identifier")
    for entry in entries:
        if not entry.effect or not entry.followup_issues or not 21 <= entry.page <= 165:
            raise ValidationError("Trait requires indexed provenance and an owned effect blocker")
        if not set(entry.prerequisites) <= identifiers or entry.id in entry.prerequisites:
            raise ValidationError("Unresolved trait prerequisite")
        binding = REACTION_BINDINGS.get(entry.id)
        if binding is not None and (binding.hook != entry.effect or not entry.implemented):
            raise ValidationError("Runtime binding disagrees with its trait effect")
        if entry.implemented and entry.obligations:
            raise ValidationError("A manual obligation is not an executable effect")
        if (entry.effect == "trait.appearance" and entry.id not in APPEARANCE_BINDINGS) or (
            entry.effect == "trait.reputation" and entry.id not in REPUTATION_BINDINGS
        ):
            raise ValidationError("Standing effect requires an exact catalog binding")
        if entry.effect in PHYSICAL_HOOKS and (
            entry.id not in PHYSICAL_BINDINGS or PHYSICAL_BINDINGS[entry.id][0] != entry.effect
        ):
            raise ValidationError("Physical effect requires an exact catalog binding")
        definition = entry.definition(entries)
        assert definition.trait_rules is not None
        validate_metadata(definition.trait_rules)


def candidate_package(vocabulary: Vocabulary = DEFAULT_VOCABULARY) -> RulesPackage:
    entries = inventory(vocabulary)
    return RulesPackage(
        "package:gurps-mundane-trait-candidates",
        "0.3.0",
        "gurps-4e",
        (SOURCE,),
        tuple(entry.definition(entries) for entry in entries),
    )


def audit_report(vocabulary: Vocabulary = DEFAULT_VOCABULARY) -> dict[str, object]:
    entries = inventory(vocabulary)
    return {
        "profile": PROFILE,
        "source": asdict(SOURCE),
        "scope": "selected mundane traits and finite campaign background identities",
        "level_bounds": "Finite candidate selection ceilings, not new universal source limits",
        "entries": [
            asdict(e) | {"reference": e.reference, "blockers": e.blockers, "status": e.status.value}
            for e in entries
        ],
        "bindings": {
            key: asdict(binding) | {"reference": binding.reference}
            for key, binding in sorted(REACTION_BINDINGS.items())
        },
        "standing_bindings": {
            "appearance": dict(APPEARANCE_BINDINGS),
            "reputation": dict(REPUTATION_BINDINGS),
        },
        "total": len(entries),
        "available": sum(e.implemented for e in entries),
        "categories": dict(sorted(Counter(e.category for e in entries).items())),
        "blockers": dict(sorted(Counter(b for e in entries for b in e.blockers).items())),
        "unbound_effects": tuple(sorted({e.effect for e in entries if not e.implemented})),
        "outside_selected_scope": (
            "native-language reductions",
            "alien cultural familiarity",
            "variable relationship constructions",
            "multimillionaire wealth",
            "setting-dependent rank prerequisites",
            "free Status from Wealth or Rank",
            "language-talent cost interactions",
            "relationship count limits and Ally/Dependent netting",
            "exotic and supernatural traits",
            "appearance special options and modifiers",
            "restricted-audience and uncertain-recognition reputation constructions",
        ),
    }
