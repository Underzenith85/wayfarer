"""Basic Set ability-modifier catalog and trusted composition rules.

The 87 definitions mirror the selected Characters third-printing lists on
B300-301.  Detailed construction rules are from B101-118.  Catalog presence,
cost construction, and runtime projection are deliberately separate: variable
GM-valued modifiers require a campaign-owned approval, and a modifier without
a runtime adapter cannot silently change an action.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Record

PROFILE: Final = "gurps-basic-set-4e-2004"


class ModifierClass(StrEnum):
    ENHANCEMENT = "enhancement"
    LIMITATION = "limitation"
    GADGET_LIMITATION = "gadget-limitation"


class CostKind(StrEnum):
    FIXED = "fixed"
    PER_LEVEL = "per-level"
    TABLE = "table"
    GADGET = "gadget"
    CAMPAIGN_APPROVAL = "campaign-approval"


AbilityKind = Literal[
    "advantage",
    "disadvantage",
    "attack",
    "affliction",
    "binding",
    "innate-attack",
    "defense",
    "ranged-attack",
]


@dataclass(frozen=True, slots=True)
class CostOption:
    id: str
    percent: int


@dataclass(frozen=True, slots=True)
class ModifierDefinition:
    id: str
    name: str
    page: int
    classification: ModifierClass
    cost_kind: CostKind
    percent: int = 0
    options: tuple[CostOption, ...] = ()
    maximum_level: int = 1
    allowed_subjects: frozenset[AbilityKind] = frozenset({"advantage"})
    requires: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    runtime_hook: str | None = None
    campaign_permission: bool = False


class GadgetConstruction(Record):
    """Construction facts used for trusted gadget limitation percentages."""

    damage_resistance: int = Field(ge=0, le=1000)
    size_modifier: int = Field(ge=-20, le=20)
    repairable: bool = True
    theft_method: Literal["grabbable", "quick-contest", "stealth-trickery", "forceful"]
    works_for_thief: bool = True
    unique: bool = False


class ModifierSelection(Record):
    definition_id: str = Field(pattern=r"^modifier:(enhancement|limitation|gadget-limitation):")
    level: int = Field(default=1, ge=1, le=100)
    option: str | None = None
    limited_by: ModifierSelection | None = None
    gadget: GadgetConstruction | None = None

    @model_validator(mode="after")
    def nested_scope(self) -> Self:
        if self.limited_by is not None and self.limited_by.limited_by is not None:
            raise ValueError("Limited enhancements may contain only one limitation")
        return self


@dataclass(frozen=True, slots=True)
class ModifierApproval:
    """Trusted campaign data for a source rule whose value is GM-defined."""

    modifier_id: str
    option: str
    percent: int
    allowed_subjects: frozenset[AbilityKind]


@dataclass(frozen=True, slots=True)
class ModifierCost:
    definition_id: str
    percent: int
    limited_by: str | None = None


@dataclass(frozen=True, slots=True)
class ModifiedCost:
    base_cost: int
    net_percent: int
    final_cost: int
    components: tuple[ModifierCost, ...]


class AttackProfile(Record):
    accuracy: int = Field(default=0, ge=0)
    max_range: int = Field(default=100, ge=0)
    area_radius: int = Field(default=0, ge=0)
    duration_seconds: int = Field(default=10, ge=0)
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0)
    fatigue_cost: int = Field(default=0, ge=0)
    activation_seconds: int = Field(default=1, ge=0)
    damage_tags: tuple[str, ...] = ()


class ModifierRuntimeReceipt(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    original: AttackProfile
    modified: AttackProfile
    applied_modifier_ids: tuple[str, ...]


class GadgetState(Record):
    held: bool = True
    broken: bool = False
    stolen: bool = False


_ALL: Final[frozenset[AbilityKind]] = frozenset(
    {"advantage", "attack", "affliction", "binding", "innate-attack", "defense", "ranged-attack"}
)
_ATTACK: Final[frozenset[AbilityKind]] = frozenset(
    {"attack", "affliction", "binding", "innate-attack", "ranged-attack"}
)
_RANGED: Final[frozenset[AbilityKind]] = frozenset(
    {"attack", "affliction", "innate-attack", "ranged-attack"}
)
_DISADVANTAGE: Final[frozenset[AbilityKind]] = frozenset({"disadvantage"})


def _classification(identifier: str) -> ModifierClass:
    return ModifierClass(identifier.split(":", 2)[1])


def _definition(
    identifier: str,
    name: str,
    page: int,
    *,
    kind: CostKind = CostKind.CAMPAIGN_APPROVAL,
    percent: int = 0,
    options: tuple[CostOption, ...] = (),
    levels: int = 1,
    subjects: frozenset[AbilityKind] = _ALL,
    requires: tuple[str, ...] = (),
    excludes: tuple[str, ...] = (),
    hook: str | None = None,
) -> ModifierDefinition:
    return ModifierDefinition(
        identifier,
        name,
        page,
        _classification(identifier),
        kind,
        percent,
        options,
        levels,
        subjects,
        requires,
        excludes,
        hook,
        kind is CostKind.CAMPAIGN_APPROVAL,
    )


def _fixed(
    identifier: str,
    name: str,
    page: int,
    percent: int,
    *,
    subjects: frozenset[AbilityKind] = _ALL,
    requires: tuple[str, ...] = (),
    excludes: tuple[str, ...] = (),
    hook: str | None = None,
) -> ModifierDefinition:
    return _definition(
        identifier,
        name,
        page,
        kind=CostKind.FIXED,
        percent=percent,
        subjects=subjects,
        requires=requires,
        excludes=excludes,
        hook=hook,
    )


def _level(
    identifier: str,
    name: str,
    page: int,
    percent: int,
    *,
    maximum: int = 100,
    subjects: frozenset[AbilityKind] = _ALL,
    excludes: tuple[str, ...] = (),
    hook: str | None = None,
) -> ModifierDefinition:
    return _definition(
        identifier,
        name,
        page,
        kind=CostKind.PER_LEVEL,
        percent=percent,
        levels=maximum,
        subjects=subjects,
        excludes=excludes,
        hook=hook,
    )


def _table(
    identifier: str,
    name: str,
    page: int,
    values: tuple[tuple[str, int], ...],
    *,
    subjects: frozenset[AbilityKind] = _ALL,
    requires: tuple[str, ...] = (),
    excludes: tuple[str, ...] = (),
    hook: str | None = None,
) -> ModifierDefinition:
    return _definition(
        identifier,
        name,
        page,
        kind=CostKind.TABLE,
        options=tuple(CostOption(*value) for value in values),
        subjects=subjects,
        requires=requires,
        excludes=excludes,
        hook=hook,
    )


AREA = "modifier:enhancement:area-effect"
CONE = "modifier:enhancement:cone"
AURA = "modifier:enhancement:aura"
JET = "modifier:enhancement:jet"
MELEE = "modifier:limitation:melee-attack"
RAPID_FIRE = "modifier:enhancement:rapid-fire"
EMANATION = "modifier:limitation:emanation"
ACCURATE = "modifier:enhancement:accurate"
INACCURATE = "modifier:limitation:inaccurate"
INCREASED_RANGE = "modifier:enhancement:increased-range"
REDUCED_RANGE = "modifier:limitation:reduced-range"
BREAKABLE = "modifier:gadget-limitation:breakable"
STOLEN = "modifier:gadget-limitation:can-be-stolen"
UNIQUE = "modifier:gadget-limitation:unique"

_CONE_EXCLUSIONS = (AREA, AURA, JET, MELEE, RAPID_FIRE, EMANATION)
_PENETRATION = (
    "modifier:enhancement:armor-divisor",
    "modifier:limitation:armor-divisor",
    "modifier:enhancement:blood-agent",
    "modifier:limitation:blood-agent",
    "modifier:enhancement:contact-agent",
    "modifier:limitation:contact-agent",
    "modifier:enhancement:follow-up",
)


_VARIABLE_ROWS: Final = (
    ("modifier:enhancement:cone", "Cone", 103),
    ("modifier:enhancement:cosmic", "Cosmic", 103),
    ("modifier:enhancement:cyclic", "Cyclic", 103),
    ("modifier:enhancement:damage-modifiers", "Damage Modifiers", 104),
    ("modifier:enhancement:delay", "Delay", 105),
    ("modifier:enhancement:follow-up", "Follow-Up", 105),
    ("modifier:enhancement:hazard", "Hazard", 104),
    ("modifier:enhancement:homing", "Homing", 105),
    ("modifier:enhancement:sense-based", "Sense-Based", 109),
    ("modifier:enhancement:side-effect", "Side Effect", 109),
    ("modifier:enhancement:symptoms", "Symptoms", 109),
    ("modifier:limitation:accessibility", "Accessibility", 110),
    ("modifier:limitation:always-on", "Always On", 110),
    ("modifier:limitation:bombardment", "Bombardment", 111),
    ("modifier:gadget-limitation:breakable", "Breakable", 117),
    ("modifier:gadget-limitation:can-be-stolen", "Can Be Stolen", 117),
    ("modifier:limitation:damage-limitations", "Damage Limitations", 111),
    ("modifier:limitation:mitigator", "Mitigator", 112),
    ("modifier:limitation:nuisance-effect", "Nuisance Effect", 112),
    ("modifier:limitation:pact", "Pact", 113),
    ("modifier:limitation:resistible", "Resistible", 115),
    ("modifier:limitation:sense-based", "Sense-Based", 115),
    ("modifier:limitation:temporary-disadvantage", "Temporary Disadvantage", 115),
    ("modifier:limitation:trigger", "Trigger", 115),
)


_DEFINITIONS: Final = (
    _level(ACCURATE, "Accurate", 102, 5, subjects=_RANGED, excludes=(INACCURATE,), hook="accuracy"),
    _fixed("modifier:enhancement:affects-insubstantial", "Affects Insubstantial", 102, 20),
    _fixed("modifier:enhancement:affects-substantial", "Affects Substantial", 102, 40),
    _level(AREA, "Area Effect", 102, 50, subjects=_ATTACK, excludes=(CONE,), hook="area"),
    _table(
        "modifier:enhancement:armor-divisor",
        "Armor Divisor",
        102,
        (("2", 50), ("3", 100), ("5", 150), ("10", 200)),
        subjects=frozenset({"affliction", "innate-attack"}),
        excludes=tuple(x for x in _PENETRATION if x != "modifier:enhancement:armor-divisor"),
        hook="armor-divisor",
    ),
    _fixed(AURA, "Aura", 102, 80, subjects=_ATTACK, requires=(MELEE,), excludes=(CONE,)),
    _fixed(
        "modifier:enhancement:based-on-different-attribute",
        "Based on (Different Attribute)",
        102,
        20,
        subjects=frozenset({"affliction", "attack"}),
    ),
    _fixed(
        "modifier:enhancement:blood-agent",
        "Blood Agent",
        102,
        100,
        subjects=_ATTACK,
        requires=(AREA,),
        excludes=tuple(x for x in _PENETRATION if x != "modifier:enhancement:blood-agent"),
    ),
    *(
        _definition(
            identifier, name, page, subjects=_ATTACK if "enhancement" in identifier else _ALL
        )
        for identifier, name, page in _VARIABLE_ROWS
    ),
    _fixed(
        "modifier:enhancement:contact-agent",
        "Contact Agent",
        103,
        150,
        subjects=_ATTACK,
        excludes=tuple(x for x in _PENETRATION if x != "modifier:enhancement:contact-agent"),
    ),
    _fixed(
        "modifier:enhancement:double-blunt-trauma-dbt",
        "Double Blunt Trauma (dbt)",
        104,
        20,
        subjects=_ATTACK,
    ),
    _fixed(
        "modifier:enhancement:double-knockback-dkb",
        "Double Knockback (dkb)",
        104,
        20,
        subjects=_ATTACK,
    ),
    _fixed(
        "modifier:enhancement:drifting", "Drifting", 105, 20, subjects=_ATTACK, requires=(AREA,)
    ),
    _level("modifier:enhancement:explosion-exp", "Explosion (exp)", 104, 50, subjects=_ATTACK),
    _table(
        "modifier:enhancement:extended-duration",
        "Extended Duration",
        105,
        (
            ("3x", 20),
            ("10x", 40),
            ("30x", 60),
            ("100x", 80),
            ("300x", 100),
            ("1000x", 120),
            ("permanent", 150),
        ),
        hook="duration",
    ),
    _level(
        "modifier:enhancement:fragmentation-frag", "Fragmentation (frag)", 104, 15, subjects=_ATTACK
    ),
    _fixed("modifier:enhancement:guided", "Guided", 105, 50, subjects=_RANGED),
    _fixed(
        "modifier:enhancement:incendiary-inc",
        "Incendiary (inc)",
        105,
        10,
        subjects=_ATTACK,
        hook="incendiary",
    ),
    _level(
        INCREASED_RANGE,
        "Increased Range",
        106,
        10,
        maximum=10,
        subjects=_RANGED,
        excludes=(REDUCED_RANGE, MELEE),
        hook="range",
    ),
    _fixed(JET, "Jet", 106, 0, subjects=_ATTACK, excludes=_CONE_EXCLUSIONS),
    _table("modifier:enhancement:link", "Link", 106, (("same-time", 10), ("selectable", 20))),
    _fixed("modifier:enhancement:low-signature", "Low Signature", 106, 10, subjects=_ATTACK),
    _table(
        "modifier:enhancement:malediction",
        "Malediction",
        106,
        (("1", 100), ("2", 150), ("3", 200)),
        subjects=frozenset({"affliction", "innate-attack"}),
        hook="resistance",
    ),
    _level("modifier:enhancement:mobile", "Mobile", 107, 40, subjects=_ATTACK, hook="mobile"),
    _fixed("modifier:enhancement:no-signature", "No Signature", 106, 20, subjects=_ATTACK),
    _fixed("modifier:enhancement:overhead", "Overhead", 107, 30, subjects=_RANGED),
    _fixed(
        "modifier:enhancement:persistent", "Persistent", 107, 40, subjects=_ATTACK, requires=(AREA,)
    ),
    _table(
        "modifier:enhancement:radiation-rad",
        "Radiation (rad)",
        105,
        (("burn-only", 25), ("fatigue-only", 100)),
        subjects=_ATTACK,
    ),
    _fixed("modifier:enhancement:ranged", "Ranged", 107, 40),
    _table(
        RAPID_FIRE,
        "Rapid Fire",
        108,
        (
            ("rof-2", 40),
            ("rof-3", 50),
            ("rof-4-7", 70),
            ("rof-8-15", 100),
            ("rof-16-30", 150),
            ("rof-31-70", 200),
            ("rof-71-150", 250),
            ("rof-151-300", 300),
        ),
        subjects=_RANGED,
        excludes=(CONE, MELEE),
    ),
    _level(
        "modifier:enhancement:reduced-fatigue-cost", "Reduced Fatigue Cost", 108, 20, hook="fatigue"
    ),
    _level("modifier:enhancement:reduced-time", "Reduced Time", 108, 20, hook="activation-time"),
    _fixed(
        "modifier:enhancement:respiratory-agent", "Respiratory Agent", 108, 50, subjects=_ATTACK
    ),
    _fixed(
        "modifier:enhancement:selective-area",
        "Selective Area",
        108,
        20,
        subjects=_ATTACK,
        requires=(AREA,),
    ),
    _fixed("modifier:enhancement:selectivity", "Selectivity", 108, 10),
    _fixed("modifier:enhancement:surge-sur", "Surge (sur)", 105, 20, subjects=_ATTACK),
    _fixed("modifier:enhancement:underwater", "Underwater", 109, 20, subjects=_ATTACK),
    _fixed("modifier:enhancement:variable", "Variable", 109, 5),
    _table(
        "modifier:enhancement:wall",
        "Wall",
        109,
        (("rigid", 30), ("permeable", 60)),
        subjects=_ATTACK,
        requires=(AREA,),
    ),
    _table(
        "modifier:limitation:armor-divisor",
        "Armor Divisor",
        110,
        (("0.5", -30), ("0.2", -50), ("0.1", -70)),
        subjects=frozenset({"innate-attack"}),
        excludes=tuple(x for x in _PENETRATION if x != "modifier:limitation:armor-divisor"),
        hook="armor-divisor",
    ),
    _fixed(
        "modifier:limitation:blood-agent",
        "Blood Agent",
        110,
        -40,
        subjects=_ATTACK,
        excludes=tuple(x for x in _PENETRATION if x != "modifier:limitation:blood-agent"),
    ),
    _fixed(
        "modifier:limitation:contact-agent",
        "Contact Agent",
        111,
        -30,
        subjects=_ATTACK,
        excludes=tuple(x for x in _PENETRATION if x != "modifier:limitation:contact-agent"),
    ),
    _level(
        "modifier:limitation:costs-fatigue",
        "Costs Fatigue",
        111,
        -5,
        subjects=_ALL,
        hook="fatigue-cost",
    ),
    _fixed(
        "modifier:limitation:dissipation",
        "Dissipation",
        112,
        -50,
        subjects=_ATTACK,
        requires=(AREA,),
    ),
    _fixed(
        EMANATION, "Emanation", 112, -20, subjects=_ATTACK, requires=(AREA,), excludes=(CONE, MELEE)
    ),
    _fixed("modifier:limitation:emergencies-only", "Emergencies Only", 112, -30),
    _level(
        "modifier:limitation:extra-recoil", "Extra Recoil", 112, -10, maximum=4, subjects=_RANGED
    ),
    _fixed(
        "modifier:limitation:full-power-in-emergencies-only",
        "Full Power in Emergencies Only",
        112,
        -20,
    ),
    _level(
        INACCURATE, "Inaccurate", 112, -5, subjects=_RANGED, excludes=(ACCURATE,), hook="accuracy"
    ),
    _table(
        "modifier:limitation:limited-use",
        "Limited Use",
        112,
        (("1-per-day", -40), ("2-per-day", -30), ("3-4-per-day", -20), ("5-10-per-day", -10)),
        hook="limited-use",
    ),
    _table(
        MELEE,
        "Melee Attack",
        112,
        (("reach-c", -30), ("reach-1-2", -25), ("reach-variable", -20), ("reach-1-4", -15)),
        subjects=_ATTACK,
        excludes=(CONE, EMANATION, RAPID_FIRE, INCREASED_RANGE),
    ),
    _fixed(
        "modifier:limitation:no-blunt-trauma-nbt",
        "No Blunt Trauma (nbt)",
        111,
        -20,
        subjects=_ATTACK,
        hook="no-blunt-trauma",
    ),
    _fixed(
        "modifier:limitation:no-knockback-nkb",
        "No Knockback (nkb)",
        111,
        -10,
        subjects=_ATTACK,
        hook="no-knockback",
    ),
    _fixed(
        "modifier:limitation:no-wounding-nw",
        "No Wounding (nw)",
        111,
        -50,
        subjects=_ATTACK,
        hook="no-wounding",
    ),
    _table(
        "modifier:limitation:onset",
        "Onset",
        113,
        (("1-minute", -10), ("1-hour", -20), ("1-day", -30), ("1-week", -40)),
        subjects=_ATTACK,
        hook="onset",
    ),
    _table(
        "modifier:limitation:preparation-required",
        "Preparation Required",
        114,
        (("1-minute", -20), ("10-minutes", -30), ("1-hour", -50), ("8-hours", -60)),
        hook="preparation",
    ),
    _level(
        REDUCED_RANGE,
        "Reduced Range",
        115,
        -10,
        maximum=3,
        subjects=_RANGED,
        excludes=(INCREASED_RANGE, MELEE),
        hook="range",
    ),
    _level(
        "modifier:limitation:takes-extra-time", "Takes Extra Time", 115, -10, hook="activation-time"
    ),
    _table(
        "modifier:limitation:takes-recharge",
        "Takes Recharge",
        115,
        (("5-seconds", -10), ("15-seconds", -20), ("1-hour", -30)),
        hook="recharge",
    ),
    _fixed("modifier:limitation:unconscious-only", "Unconscious Only", 115, -20),
    _table(
        "modifier:limitation:uncontrollable",
        "Uncontrollable",
        116,
        (("ordinary", -10), ("dangerous", -30)),
    ),
    _fixed(UNIQUE, "Unique", 117, -25, requires=(BREAKABLE,), hook="gadget"),
    _table(
        "modifier:limitation:unreliable",
        "Unreliable",
        116,
        (
            ("activation-5", -80),
            ("activation-8", -40),
            ("activation-11", -20),
            ("activation-14", -10),
            ("malf-12", -25),
            ("malf-13", -20),
            ("malf-14", -15),
            ("malf-15", -10),
            ("malf-16", -5),
        ),
        hook="unreliable",
    ),
    _fixed("modifier:limitation:untrainable", "Untrainable", 116, -40),
)


def _deduplicate_and_patch() -> tuple[ModifierDefinition, ...]:
    """Variable placeholders are replaced by trusted table definitions above."""
    result: dict[str, ModifierDefinition] = {}
    for definition in _DEFINITIONS:
        existing = result.get(definition.id)
        if existing is None or existing.cost_kind is CostKind.CAMPAIGN_APPROVAL:
            result[definition.id] = definition
    result[CONE] = replace(result[CONE], allowed_subjects=_ATTACK, excludes=_CONE_EXCLUSIONS)
    follow_up = "modifier:enhancement:follow-up"
    result[follow_up] = replace(
        result[follow_up],
        allowed_subjects=_ATTACK,
        excludes=tuple(item for item in _PENETRATION if item != follow_up),
    )
    bombardment = "modifier:limitation:bombardment"
    result[bombardment] = replace(result[bombardment], allowed_subjects=_ATTACK, requires=(AREA,))
    mitigator = "modifier:limitation:mitigator"
    result[mitigator] = replace(result[mitigator], allowed_subjects=_DISADVANTAGE)
    result[BREAKABLE] = replace(
        result[BREAKABLE],
        cost_kind=CostKind.GADGET,
        campaign_permission=False,
        runtime_hook="gadget",
    )
    result[STOLEN] = replace(
        result[STOLEN],
        cost_kind=CostKind.GADGET,
        campaign_permission=False,
        runtime_hook="gadget",
    )
    # Unique is valid with either gadget-loss route; cross-row OR
    # prerequisites are checked explicitly in ``validate_selections``.
    result[UNIQUE] = replace(result[UNIQUE], requires=())
    return tuple(result.values())


MODIFIERS: Final = _deduplicate_and_patch()
MODIFIER_INDEX: Final = MappingProxyType({definition.id: definition for definition in MODIFIERS})


def _percentage(
    selection: ModifierSelection,
    approvals: tuple[ModifierApproval, ...],
) -> int:
    definition = MODIFIER_INDEX.get(selection.definition_id)
    if definition is None:
        raise ValidationError("Unknown ability modifier")
    if (
        selection.gadget is not None
        and definition.cost_kind is not CostKind.GADGET
        and selection.definition_id != UNIQUE
    ):
        raise ValidationError("Gadget facts are not applicable to this modifier")
    if definition.cost_kind is CostKind.FIXED:
        if (
            selection.level != 1
            or selection.option is not None
            or (selection.gadget is not None and selection.definition_id != UNIQUE)
        ):
            raise ValidationError("Fixed modifier takes no level or option")
        value = definition.percent
    elif definition.cost_kind is CostKind.PER_LEVEL:
        if selection.level > definition.maximum_level or selection.option is not None:
            raise ValidationError("Modifier level is outside its source bounds")
        value = definition.percent * selection.level
    elif definition.cost_kind is CostKind.TABLE:
        options = {option.id: option.percent for option in definition.options}
        if selection.level != 1 or selection.option not in options:
            raise ValidationError("Modifier requires one trusted table option")
        value = options[selection.option]
    elif definition.cost_kind is CostKind.GADGET:
        if selection.level != 1 or selection.option is not None or selection.gadget is None:
            raise ValidationError("Gadget limitation requires explicit construction facts")
        gadget = selection.gadget
        if selection.definition_id == BREAKABLE:
            durability = (
                -20
                if gadget.damage_resistance <= 2
                else -15
                if gadget.damage_resistance <= 5
                else -10
                if gadget.damage_resistance <= 15
                else -5
                if gadget.damage_resistance <= 25
                else 0
            )
            size = (
                0
                if gadget.size_modifier <= -9
                else -5
                if gadget.size_modifier <= -7
                else -10
                if gadget.size_modifier <= -5
                else -15
                if gadget.size_modifier <= -3
                else -20
                if gadget.size_modifier <= -1
                else -25
            )
            value = durability + size + (0 if gadget.repairable else -15)
        elif selection.definition_id == STOLEN:
            value = {
                "grabbable": -40,
                "quick-contest": -30,
                "stealth-trickery": -20,
                "forceful": -10,
            }[gadget.theft_method]
            if not gadget.works_for_thief:
                value = int(Decimal(value) / 2)
        else:
            raise ValidationError("Unknown gadget cost adapter")
    else:
        matches = tuple(
            approval
            for approval in approvals
            if approval.modifier_id == selection.definition_id
            and approval.option == selection.option
        )
        if selection.level != 1 or len(matches) != 1:
            raise ValidationError("Modifier requires an exact campaign approval")
        value = matches[0].percent
        if definition.classification is ModifierClass.ENHANCEMENT and value < 0:
            raise ValidationError("Enhancement approval cannot reduce cost")
        if definition.classification is not ModifierClass.ENHANCEMENT and value > 0:
            raise ValidationError("Limitation approval cannot increase cost")
    if selection.limited_by is not None:
        if definition.classification is not ModifierClass.ENHANCEMENT:
            raise ValidationError("Only an enhancement may have a limited scope")
        limitation = MODIFIER_INDEX.get(selection.limited_by.definition_id)
        if limitation is None or limitation.classification is ModifierClass.ENHANCEMENT:
            raise ValidationError("Limited enhancement scope must be a limitation")
        reduction = max(-80, _percentage(selection.limited_by, approvals))
        value = int(
            (Decimal(value) * Decimal(100 + reduction) / 100).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
    return value


def validate_selections(
    subject: AbilityKind,
    selections: tuple[ModifierSelection, ...],
    approvals: tuple[ModifierApproval, ...] = (),
) -> tuple[ModifierCost, ...]:
    identifiers = tuple(selection.definition_id for selection in selections)
    if len(identifiers) != len(set(identifiers)):
        raise ValidationError("Duplicate ability modifier")
    selected = set(identifiers)
    costs: list[ModifierCost] = []
    approval_index = {(approval.modifier_id, approval.option): approval for approval in approvals}
    if len(approval_index) != len(approvals):
        raise ValidationError("Duplicate campaign modifier approval")
    for selection in selections:
        definition = MODIFIER_INDEX.get(selection.definition_id)
        if definition is None or subject not in definition.allowed_subjects:
            raise ValidationError("Modifier is unavailable for this ability kind")
        if not set(definition.requires) <= selected:
            raise ValidationError("Modifier prerequisite is absent")
        if set(definition.excludes) & selected:
            raise ValidationError("Incompatible ability modifiers")
        if selection.definition_id == UNIQUE and not {BREAKABLE, STOLEN} & selected:
            raise ValidationError("Unique requires Breakable or Can Be Stolen")
        if selection.definition_id == UNIQUE:
            gadget_facts = tuple(
                candidate.gadget
                for candidate in selections
                if candidate.definition_id in {BREAKABLE, STOLEN} and candidate.gadget is not None
            )
            if not gadget_facts or not all(facts.unique for facts in gadget_facts):
                raise ValidationError("Unique requires gadget facts marked unique")
        approval = approval_index.get((selection.definition_id, selection.option or ""))
        if approval is not None and subject not in approval.allowed_subjects:
            raise ValidationError("Campaign approval does not allow this ability kind")
        costs.append(
            ModifierCost(
                selection.definition_id,
                _percentage(selection, approvals),
                None if selection.limited_by is None else selection.limited_by.definition_id,
            )
        )
    return tuple(costs)


def modified_cost(
    base_cost: int,
    subject: AbilityKind,
    selections: tuple[ModifierSelection, ...],
    approvals: tuple[ModifierApproval, ...] = (),
) -> ModifiedCost:
    if type(base_cost) is not int:
        raise ValidationError("Ability base cost must be an integer")
    components = validate_selections(subject, selections, approvals)
    net = max(-80, sum(component.percent for component in components))
    final = int(
        (Decimal(base_cost) * Decimal(100 + net) / 100).to_integral_value(rounding=ROUND_CEILING)
    )
    return ModifiedCost(base_cost, net, final, components)


@dataclass(frozen=True, slots=True)
class AbilityDefinition:
    id: str
    kind: AbilityKind
    base_cost: int
    modifiers: tuple[ModifierSelection, ...] = ()

    def cost(self, approvals: tuple[ModifierApproval, ...] = ()) -> ModifiedCost:
        return modified_cost(self.base_cost, self.kind, self.modifiers, approvals)


@dataclass(frozen=True, slots=True)
class AlternativeAbilities:
    """Composed alternatives: full price for the most costly, one fifth for others."""

    abilities: tuple[AbilityDefinition, ...]

    def cost(self, approvals: tuple[ModifierApproval, ...] = ()) -> int:
        if len(self.abilities) < 2 or len({ability.id for ability in self.abilities}) != len(
            self.abilities
        ):
            raise ValidationError("Alternative abilities require distinct composed definitions")
        costs = sorted(
            (ability.cost(approvals).final_cost for ability in self.abilities), reverse=True
        )
        return costs[0] + sum((cost + 4) // 5 for cost in costs[1:])


def gadget_available(selections: tuple[ModifierSelection, ...], state: GadgetState) -> bool:
    selected = {selection.definition_id for selection in selections}
    if not selected & {BREAKABLE, STOLEN, UNIQUE}:
        return True
    return state.held and not state.broken and not state.stolen


def apply_attack_modifiers(
    profile: AttackProfile,
    subject: AbilityKind,
    selections: tuple[ModifierSelection, ...],
    approvals: tuple[ModifierApproval, ...] = (),
) -> ModifierRuntimeReceipt:
    """Project selected executable modifiers into authoritative attack facts."""
    validate_selections(subject, selections, approvals)
    if subject not in _ATTACK:
        raise ValidationError("Attack modifier projection requires an attack ability")
    result = profile
    range_multipliers = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)
    for selection in selections:
        definition = MODIFIER_INDEX[selection.definition_id]
        if definition.runtime_hook == "accuracy":
            delta = selection.level * (
                1 if definition.classification is ModifierClass.ENHANCEMENT else -1
            )
            if result.accuracy + delta < 0:
                raise ValidationError("Inaccurate cannot reduce Accuracy below zero")
            result = result.model_copy(update={"accuracy": result.accuracy + delta})
        elif definition.runtime_hook == "range":
            divisor_or_multiplier = range_multipliers[selection.level]
            value = (
                result.max_range * divisor_or_multiplier
                if definition.classification is ModifierClass.ENHANCEMENT
                else result.max_range // divisor_or_multiplier
            )
            result = result.model_copy(update={"max_range": value})
        elif definition.runtime_hook == "area":
            result = result.model_copy(update={"area_radius": 2**selection.level})
        elif definition.runtime_hook == "duration":
            multipliers = {"3x": 3, "10x": 10, "30x": 30, "100x": 100, "300x": 300, "1000x": 1000}
            if selection.option == "permanent":
                result = result.model_copy(update={"duration_seconds": 0})
            else:
                result = result.model_copy(
                    update={
                        "duration_seconds": result.duration_seconds
                        * multipliers[selection.option or ""]
                    }
                )
        elif definition.runtime_hook == "armor-divisor":
            result = result.model_copy(update={"armor_divisor": Decimal(selection.option or "1")})
        elif definition.runtime_hook == "fatigue":
            result = result.model_copy(
                update={"fatigue_cost": max(0, result.fatigue_cost - selection.level)}
            )
        elif definition.runtime_hook == "fatigue-cost":
            result = result.model_copy(
                update={"fatigue_cost": result.fatigue_cost + selection.level}
            )
        elif definition.runtime_hook == "activation-time":
            seconds = (
                max(0, result.activation_seconds // (2**selection.level))
                if definition.classification is ModifierClass.ENHANCEMENT
                else result.activation_seconds * (2**selection.level)
            )
            result = result.model_copy(update={"activation_seconds": seconds})
        elif definition.runtime_hook in {
            "incendiary",
            "no-blunt-trauma",
            "no-knockback",
            "no-wounding",
        }:
            result = result.model_copy(
                update={"damage_tags": result.damage_tags + (definition.runtime_hook,)}
            )
    return ModifierRuntimeReceipt(
        original=profile,
        modified=result,
        applied_modifier_ids=tuple(selection.definition_id for selection in selections),
    )


def validate_catalog() -> None:
    if len(MODIFIERS) != 87 or len(MODIFIER_INDEX) != 87:
        raise ValidationError("Basic Set modifier catalog denominator drift")
    for definition in MODIFIERS:
        if not definition.id or not definition.name or not 101 <= definition.page <= 118:
            raise ValidationError("Invalid Basic Set modifier provenance")
        if definition.cost_kind is CostKind.TABLE and not definition.options:
            raise ValidationError("Table modifier lacks trusted options")
        if definition.campaign_permission != (definition.cost_kind is CostKind.CAMPAIGN_APPROVAL):
            raise ValidationError("Modifier permission and cost semantics disagree")
        if definition.classification is ModifierClass.ENHANCEMENT and definition.percent < 0:
            raise ValidationError("Enhancement has a negative fixed contribution")
        if definition.classification is not ModifierClass.ENHANCEMENT and definition.percent > 0:
            raise ValidationError("Limitation has a positive fixed contribution")
        if not set((*definition.requires, *definition.excludes)) <= MODIFIER_INDEX.keys():
            raise ValidationError("Modifier relationship references an unknown definition")


validate_catalog()
