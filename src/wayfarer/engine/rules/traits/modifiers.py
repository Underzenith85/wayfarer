"""Basic Set ability-modifier catalog and trusted composition rules.

The 87 definitions mirror the selected Characters third-printing lists on
B300-301.  Detailed construction rules are from B101-118.  Catalog presence,
cost construction, and runtime projection are deliberately separate: variable
GM-valued modifiers require a campaign-owned approval, and a modifier without
a runtime adapter cannot silently change an action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import NO_RANDOM, CheckTrace, RandomSource, success_check
from wayfarer.engine.rules.types.affliction import AfflictionDelivery, PenetrationModifier
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
    selectable: bool = True


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
    parameters: EnhancementParameters | None = None
    limitation: LimitationParameters | None = None

    @model_validator(mode="after")
    def nested_scope(self) -> Self:
        if self.limited_by is not None and self.limited_by.limited_by is not None:
            raise ValueError("Limited enhancements may contain only one limitation")
        return self


class EnhancementParameters(Record):
    """Source-bounded authored facts for variable enhancement consequences.

    Cost approval and runtime meaning are intentionally separate.  In particular,
    approving a percentage never grants an unnamed Cosmic bypass or an arbitrary
    scheduled effect.
    """

    width_yards: int | None = Field(default=None, ge=1, le=1000)
    cosmic_effect: (
        Literal[
            "remove-built-in-restriction",
            "cosmic-defense",
            "enduring-effect",
            "irresistible-attack",
        ]
        | None
    ) = None
    interval_seconds: int | None = Field(default=None, ge=1, le=86_400)
    cycles: int | None = Field(default=None, ge=2, le=1000)
    contagious: Literal["none", "mild", "high"] | None = None
    stop_condition: str | None = Field(default=None, min_length=1, max_length=200)
    delay_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    variable_delay_max_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    trigger: str | None = Field(default=None, min_length=1, max_length=200)
    carrier_id: str | None = Field(default=None, min_length=1, max_length=100)
    homing_sense: str | None = Field(default=None, min_length=1, max_length=100)
    linked_ability_id: str | None = Field(default=None, min_length=1, max_length=100)
    rate_of_fire: int | None = Field(default=None, ge=2, le=300)
    selective_fire: bool = False
    resistance_attribute: Literal["st", "dx", "iq", "ht", "per", "will"] | None = None
    hazard: (
        Literal["dehydration", "drowning", "freezing", "missed-sleep", "starvation", "suffocation"]
        | None
    ) = None
    symptom: str | None = Field(default=None, min_length=1, max_length=100)
    symptom_threshold: Literal["one-third", "one-half", "two-thirds"] | None = None
    permanent_end_condition: str | None = Field(default=None, min_length=1, max_length=200)
    disabled_enhancements: tuple[str, ...] = ()
    damage_fraction: Decimal | None = Field(default=None, gt=0, le=1)
    damage_kind: (
        Literal[
            "burning",
            "corrosion",
            "crushing",
            "cutting",
            "fatigue",
            "impaling",
            "piercing",
            "toxic",
        ]
        | None
    ) = None


class LimitationParameters(Record):
    """Authored facts which give a variable limitation exact runtime meaning."""

    condition_id: str | None = Field(default=None, min_length=1, max_length=200)
    always_on_severity: Literal["cosmetic", "inconvenient", "dangerous"] | None = None
    bombardment_skill: Literal[8, 10, 12, 14] | None = None
    mitigator_id: str | None = Field(default=None, min_length=1, max_length=100)
    nuisance_effect_id: str | None = Field(default=None, min_length=1, max_length=200)
    pact_id: str | None = Field(default=None, min_length=1, max_length=100)
    preparation_weakened: bool = False
    resistance_modifier: int | None = Field(default=None, ge=-5, le=4)
    sense: str | None = Field(default=None, min_length=1, max_length=100)
    temporary_disadvantage_ids: tuple[str, ...] = ()
    trigger_id: str | None = Field(default=None, min_length=1, max_length=100)
    trigger_dangerous: bool = False
    exposure_time: bool = False
    half_damage_range_only: bool = False
    limited_use_reload: Literal["none", "fast", "slow"] = "none"
    uncontrollable_harmful: bool = False


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
    damage_kind: Literal[
        "burning", "corrosion", "crushing", "cutting", "fatigue", "impaling", "piercing", "toxic"
    ] = "crushing"
    accuracy: int = Field(default=0, ge=0)
    max_range: int = Field(default=100, ge=0)
    area_radius: int = Field(default=0, ge=0)
    duration_seconds: int = Field(default=10, ge=0)
    duration_end_condition: str | None = None
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0)
    fatigue_cost: int = Field(default=0, ge=0)
    activation_seconds: int = Field(default=1, ge=0)
    damage_tags: tuple[str, ...] = ()
    penetration_modifier: PenetrationModifier = "ordinary"
    penetration_sense: str | None = None
    affliction_delivery: AfflictionDelivery = "direct"
    half_damage_range: int = Field(default=10, ge=0)
    affects_substantial: bool = True
    affects_insubstantial: bool = False
    usable_while_insubstantial: bool = False
    is_ranged: bool = True
    cone_width_yards: int | None = Field(default=None, ge=1)
    aura: bool = False
    resistance_attribute: Literal["st", "dx", "iq", "ht", "per", "will"] = "ht"
    cosmic_effect: str | None = None
    cyclic_interval_seconds: int | None = Field(default=None, ge=1)
    cyclic_cycles: int = Field(default=1, ge=1)
    cyclic_stop_condition: str | None = None
    contagious: Literal["none", "mild", "high"] = "none"
    delay_seconds: int = Field(default=0, ge=0)
    delay_trigger: str | None = None
    variable_delay_max_seconds: int | None = Field(default=None, ge=1)
    blunt_trauma_multiplier: int = Field(default=1, ge=0)
    knockback_multiplier: int = Field(default=1, ge=0)
    explosion_divisor: int | None = Field(default=None, ge=1, le=3)
    fragmentation_dice: int = Field(default=0, ge=0, le=12)
    guidance: Literal["none", "guided", "homing"] = "none"
    homing_sense: str | None = None
    hazard: str | None = None
    carrier_id: str | None = None
    jet: bool = False
    linked_ability_id: str | None = None
    link_selectable: bool = False
    signature: Literal["normal", "low", "none"] = "normal"
    malediction_range: Literal["none", "yards", "speed-range", "long-distance"] = "none"
    mobile_move: int = Field(default=0, ge=0)
    overhead: bool = False
    persistent: bool = False
    drifting: bool = False
    radiation_mode: Literal["none", "instead-of-damage", "additional"] = "none"
    rate_of_fire: int = Field(default=1, ge=1, le=300)
    selective_fire: bool = False
    selective_area: bool = False
    switchable_enhancements: bool = False
    disabled_enhancements: tuple[str, ...] = ()
    surge: bool = False
    symptom: str | None = None
    symptom_threshold: str | None = None
    underwater_range_divisor: int | None = Field(default=None, ge=1)
    variable_damage_fraction: Decimal = Field(default=Decimal(1), gt=0, le=1)
    wall: Literal["none", "rigid", "permeable"] = "none"
    wall_shape_flexible: bool = False
    wall_dr_per_die: int = Field(default=0, ge=0)
    wall_hp_per_die: Decimal = Field(default=Decimal(0), ge=0)
    recoil: int = Field(default=1, ge=1, le=5)
    reach: str | None = None
    parry_allowed: bool = True
    wounding: bool = True
    preparation_seconds: int = Field(default=0, ge=0)
    recharge_seconds: int = Field(default=0, ge=0)
    uses_per_day: int | None = Field(default=None, ge=1, le=10)
    onset_seconds: int = Field(default=0, ge=0)
    onset_requires_continuous_exposure: bool = False
    resistance_modifier: int | None = Field(default=None, ge=-5, le=4)
    bombardment_skill: int | None = Field(default=None, ge=8, le=14)
    dissipates_with_distance: bool = False
    always_on: bool = False
    trained_control_allowed: bool = True


class ModifierRuntimeReceipt(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    original: AttackProfile
    modified: AttackProfile
    applied_modifier_ids: tuple[str, ...]


class GadgetState(Record):
    held: bool = True
    broken: bool = False
    stolen: bool = False


class LimitationContext(Record):
    """Complete authoritative facts for one attempted ability use."""

    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    actor_id: str = Field(min_length=1, max_length=200)
    ability_id: str = Field(min_length=1, max_length=200)
    now: int = Field(ge=0)
    fp_available: int = Field(default=0, ge=0)
    will: int = Field(default=10, ge=1, le=30)
    conscious: bool = True
    emergency: bool = False
    satisfied_condition_ids: tuple[str, ...] = ()
    available_mitigator_ids: tuple[str, ...] = ()
    observed_pact_ids: tuple[str, ...] = ()
    supplied_trigger_ids: tuple[str, ...] = ()
    prepared_ability_id: str | None = None
    prepared_at: int | None = Field(default=None, ge=0)
    last_used_at: int | None = Field(default=None, ge=0)
    uses_today: int = Field(default=0, ge=0)
    gadget: GadgetState | None = None


class LimitationRuntimeReceipt(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    actor_id: str
    ability_id: str
    at: int
    available: bool
    controlled: bool = True
    power_fraction: Decimal = Decimal(1)
    fatigue_cost: int = 0
    next_available_at: int | None = None
    temporary_disadvantage_ids: tuple[str, ...] = ()
    runtime_effect_ids: tuple[str, ...] = ()
    checks: tuple[CheckTrace, ...] = ()
    applied_modifier_ids: tuple[str, ...] = ()


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
    "modifier:enhancement:respiratory-agent",
    "modifier:enhancement:sense-based",
    "modifier:limitation:sense-based",
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
        (("toxic", 25), ("burning", 100)),
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
    result[JET] = replace(
        result[JET], excludes=tuple(item for item in _CONE_EXCLUSIONS if item != JET)
    )
    for identifier in _PENETRATION:
        result[identifier] = replace(
            result[identifier],
            allowed_subjects=_ATTACK,
            excludes=tuple(item for item in _PENETRATION if item != identifier),
        )
    for identifier, hook in (
        ("modifier:enhancement:blood-agent", "blood-agent"),
        ("modifier:limitation:blood-agent", "blood-agent"),
        ("modifier:enhancement:contact-agent", "contact-agent"),
        ("modifier:limitation:contact-agent", "contact-agent"),
        ("modifier:enhancement:follow-up", "follow-up"),
        ("modifier:enhancement:respiratory-agent", "respiratory-agent"),
        ("modifier:enhancement:sense-based", "sense-based"),
        ("modifier:limitation:sense-based", "sense-based"),
    ):
        result[identifier] = replace(result[identifier], runtime_hook=hook)
    enhancement_hooks = {
        "modifier:enhancement:affects-insubstantial": "affects-insubstantial",
        "modifier:enhancement:affects-substantial": "affects-substantial",
        AURA: "aura",
        "modifier:enhancement:based-on-different-attribute": "resistance-attribute",
        CONE: "cone",
        "modifier:enhancement:cosmic": "cosmic",
        "modifier:enhancement:cyclic": "cyclic",
        "modifier:enhancement:delay": "delay",
        "modifier:enhancement:double-blunt-trauma-dbt": "double-blunt-trauma",
        "modifier:enhancement:double-knockback-dkb": "double-knockback",
        "modifier:enhancement:drifting": "drifting",
        "modifier:enhancement:explosion-exp": "explosion",
        "modifier:enhancement:fragmentation-frag": "fragmentation",
        "modifier:enhancement:guided": "guided",
        "modifier:enhancement:hazard": "hazard",
        "modifier:enhancement:homing": "homing",
        JET: "jet",
        "modifier:enhancement:link": "link",
        "modifier:enhancement:low-signature": "low-signature",
        "modifier:enhancement:malediction": "malediction",
        "modifier:enhancement:mobile": "mobile",
        "modifier:enhancement:no-signature": "no-signature",
        "modifier:enhancement:overhead": "overhead",
        "modifier:enhancement:persistent": "persistent",
        "modifier:enhancement:radiation-rad": "radiation",
        "modifier:enhancement:ranged": "ranged",
        RAPID_FIRE: "rapid-fire",
        "modifier:enhancement:selective-area": "selective-area",
        "modifier:enhancement:selectivity": "selectivity",
        "modifier:enhancement:surge-sur": "surge",
        "modifier:enhancement:symptoms": "symptoms",
        "modifier:enhancement:underwater": "underwater",
        "modifier:enhancement:variable": "variable",
        "modifier:enhancement:wall": "wall",
    }
    for identifier, hook in enhancement_hooks.items():
        result[identifier] = replace(result[identifier], runtime_hook=hook)
    result["modifier:enhancement:damage-modifiers"] = replace(
        result["modifier:enhancement:damage-modifiers"],
        cost_kind=CostKind.FIXED,
        percent=0,
        campaign_permission=False,
        runtime_hook="damage-modifier-family",
        selectable=False,
    )
    result["modifier:enhancement:cyclic"] = replace(
        result["modifier:enhancement:cyclic"],
        allowed_subjects=frozenset({"innate-attack"}),
    )
    result["modifier:enhancement:hazard"] = replace(
        result["modifier:enhancement:hazard"],
        allowed_subjects=frozenset({"innate-attack"}),
    )
    result["modifier:enhancement:homing"] = replace(
        result["modifier:enhancement:homing"],
        allowed_subjects=_RANGED,
        excludes=("modifier:enhancement:guided",),
    )
    result["modifier:enhancement:guided"] = replace(
        result["modifier:enhancement:guided"], excludes=("modifier:enhancement:homing",)
    )
    result["modifier:enhancement:mobile"] = replace(
        result["modifier:enhancement:mobile"],
        requires=(AREA, "modifier:enhancement:persistent"),
        excludes=("modifier:enhancement:drifting",),
    )
    result["modifier:enhancement:persistent"] = replace(
        result["modifier:enhancement:persistent"], requires=(AREA,)
    )
    result["modifier:enhancement:wall"] = replace(
        result["modifier:enhancement:wall"],
        requires=(AREA, "modifier:enhancement:persistent"),
    )
    result["modifier:enhancement:symptoms"] = replace(
        result["modifier:enhancement:symptoms"],
        allowed_subjects=frozenset({"innate-attack"}),
    )
    result["modifier:enhancement:drifting"] = replace(
        result["modifier:enhancement:drifting"], requires=()
    )
    result["modifier:enhancement:selective-area"] = replace(
        result["modifier:enhancement:selective-area"], requires=()
    )
    result["modifier:enhancement:ranged"] = replace(
        result["modifier:enhancement:ranged"], allowed_subjects=frozenset({"advantage"})
    )
    result["modifier:enhancement:reduced-time"] = replace(
        result["modifier:enhancement:reduced-time"],
        allowed_subjects=frozenset({"advantage", "defense"}),
    )
    result[INCREASED_RANGE] = replace(
        result[INCREASED_RANGE], allowed_subjects=_RANGED | frozenset({"advantage"})
    )
    result["modifier:enhancement:low-signature"] = replace(
        result["modifier:enhancement:low-signature"],
        excludes=("modifier:enhancement:no-signature",),
    )
    result["modifier:enhancement:no-signature"] = replace(
        result["modifier:enhancement:no-signature"],
        excludes=("modifier:enhancement:low-signature",),
    )
    for identifier in (
        "modifier:enhancement:blood-agent",
        "modifier:enhancement:contact-agent",
        "modifier:enhancement:respiratory-agent",
    ):
        result[identifier] = replace(result[identifier], requires=())
    result["modifier:enhancement:explosion-exp"] = replace(
        result["modifier:enhancement:explosion-exp"], maximum_level=3
    )
    result["modifier:enhancement:fragmentation-frag"] = replace(
        result["modifier:enhancement:fragmentation-frag"], maximum_level=12
    )
    result["modifier:enhancement:radiation-rad"] = replace(
        result["modifier:enhancement:radiation-rad"],
        options=(CostOption("toxic", 25), CostOption("burning", 100)),
        allowed_subjects=frozenset({"innate-attack"}),
    )
    side_effect = "modifier:enhancement:side-effect"
    result[side_effect] = replace(
        result[side_effect],
        allowed_subjects=frozenset({"innate-attack"}),
        excludes=tuple(
            identifier
            for identifier in _PENETRATION
            if identifier
            not in {
                "modifier:enhancement:armor-divisor",
                "modifier:limitation:armor-divisor",
            }
        ),
        runtime_hook="side-effect",
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
    limitation_hooks = {
        "modifier:limitation:accessibility": "accessibility",
        "modifier:limitation:always-on": "always-on",
        "modifier:limitation:bombardment": "bombardment",
        "modifier:limitation:dissipation": "dissipation",
        EMANATION: "emanation",
        "modifier:limitation:emergencies-only": "emergencies-only",
        "modifier:limitation:extra-recoil": "extra-recoil",
        "modifier:limitation:full-power-in-emergencies-only": "emergency-power",
        "modifier:limitation:limited-use": "limited-use",
        MELEE: "melee",
        "modifier:limitation:mitigator": "mitigator",
        "modifier:limitation:nuisance-effect": "nuisance",
        "modifier:limitation:onset": "onset",
        "modifier:limitation:pact": "pact",
        "modifier:limitation:preparation-required": "preparation",
        "modifier:limitation:resistible": "resistible",
        "modifier:limitation:takes-recharge": "recharge",
        "modifier:limitation:temporary-disadvantage": "temporary-disadvantage",
        "modifier:limitation:trigger": "trigger",
        "modifier:limitation:unconscious-only": "unconscious-only",
        "modifier:limitation:uncontrollable": "uncontrollable",
        "modifier:limitation:unreliable": "unreliable",
        "modifier:limitation:untrainable": "untrainable",
        BREAKABLE: "gadget",
        STOLEN: "gadget",
        UNIQUE: "gadget",
    }
    for identifier, hook in limitation_hooks.items():
        result[identifier] = replace(result[identifier], runtime_hook=hook)
    result["modifier:limitation:bombardment"] = replace(
        result["modifier:limitation:bombardment"],
        cost_kind=CostKind.TABLE,
        options=tuple(
            CostOption(str(skill), value)
            for skill, value in ((14, -5), (12, -10), (10, -15), (8, -20))
        ),
        campaign_permission=False,
    )
    result["modifier:limitation:always-on"] = replace(
        result["modifier:limitation:always-on"],
        cost_kind=CostKind.TABLE,
        options=(
            CostOption("cosmetic", -10),
            CostOption("inconvenient", -20),
            CostOption("dangerous", -40),
        ),
        campaign_permission=False,
    )
    result["modifier:limitation:resistible"] = replace(
        result["modifier:limitation:resistible"],
        cost_kind=CostKind.TABLE,
        options=tuple(
            CostOption(f"ht{modifier:+d}", -5 * (modifier + 6)) for modifier in range(-5, 5)
        ),
        campaign_permission=False,
        allowed_subjects=frozenset({"innate-attack"}),
    )
    result["modifier:limitation:trigger"] = replace(
        result["modifier:limitation:trigger"],
        cost_kind=CostKind.TABLE,
        options=tuple(
            CostOption(option, value)
            for option, value in (
                ("very-common", -10),
                ("common", -20),
                ("occasional", -30),
                ("rare", -40),
                ("very-common-dangerous", -15),
                ("common-dangerous", -30),
                ("occasional-dangerous", -45),
                ("rare-dangerous", -60),
            )
        ),
        campaign_permission=False,
    )
    result["modifier:limitation:damage-limitations"] = replace(
        result["modifier:limitation:damage-limitations"],
        cost_kind=CostKind.FIXED,
        percent=0,
        campaign_permission=False,
        runtime_hook="damage-limitation-family",
        selectable=False,
    )
    result["modifier:limitation:unconscious-only"] = replace(
        result["modifier:limitation:unconscious-only"],
        requires=("modifier:limitation:uncontrollable",),
    )
    result["modifier:limitation:onset"] = replace(
        result["modifier:limitation:onset"],
        requires=(),
    )
    return tuple(result.values())


MODIFIERS: Final = _deduplicate_and_patch()
MODIFIER_INDEX: Final = MappingProxyType({definition.id: definition for definition in MODIFIERS})


def _fixed_percentage(
    selection: ModifierSelection,
    definition: ModifierDefinition,
    approvals: tuple[ModifierApproval, ...],
) -> int:
    del approvals
    if (
        selection.level != 1
        or selection.option is not None
        or (selection.gadget is not None and selection.definition_id != UNIQUE)
    ):
        raise ValidationError("Fixed modifier takes no level or option")
    return definition.percent


def _per_level_percentage(
    selection: ModifierSelection,
    definition: ModifierDefinition,
    approvals: tuple[ModifierApproval, ...],
) -> int:
    del approvals
    if selection.level > definition.maximum_level or selection.option is not None:
        raise ValidationError("Modifier level is outside its source bounds")
    return definition.percent * selection.level


def _table_percentage(
    selection: ModifierSelection,
    definition: ModifierDefinition,
    approvals: tuple[ModifierApproval, ...],
) -> int:
    del approvals
    options = {option.id: option.percent for option in definition.options}
    if selection.level != 1 or selection.option not in options:
        raise ValidationError("Modifier requires one trusted table option")
    value = options[selection.option]
    if selection.definition_id == RAPID_FIRE and selection.parameters is not None:
        value += 10 if selection.parameters.selective_fire else 0
    return value


def _breakable_percentage(gadget: GadgetConstruction) -> int:
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
    return durability + size + (0 if gadget.repairable else -15)


def _stolen_percentage(gadget: GadgetConstruction) -> int:
    value = {
        "grabbable": -40,
        "quick-contest": -30,
        "stealth-trickery": -20,
        "forceful": -10,
    }[gadget.theft_method]
    return value if gadget.works_for_thief else int(Decimal(value) / 2)


_GADGET_PERCENTAGES: Final[dict[str, Callable[[GadgetConstruction], int]]] = {
    BREAKABLE: _breakable_percentage,
    STOLEN: _stolen_percentage,
}


def _gadget_percentage(
    selection: ModifierSelection,
    definition: ModifierDefinition,
    approvals: tuple[ModifierApproval, ...],
) -> int:
    del definition, approvals
    if selection.level != 1 or selection.option is not None or selection.gadget is None:
        raise ValidationError("Gadget limitation requires explicit construction facts")
    resolver = _GADGET_PERCENTAGES.get(selection.definition_id)
    if resolver is None:
        raise ValidationError("Unknown gadget cost adapter")
    return resolver(selection.gadget)


def _approved_percentage(
    selection: ModifierSelection,
    definition: ModifierDefinition,
    approvals: tuple[ModifierApproval, ...],
) -> int:
    matches = tuple(
        approval
        for approval in approvals
        if approval.modifier_id == selection.definition_id and approval.option == selection.option
    )
    if selection.level != 1 or len(matches) != 1:
        raise ValidationError("Modifier requires an exact campaign approval")
    value = matches[0].percent
    if selection.definition_id == "modifier:enhancement:cosmic":
        params = selection.parameters
        cosmic_values = {
            "remove-built-in-restriction": 50,
            "cosmic-defense": 50,
            "enduring-effect": 100,
            "irresistible-attack": 300,
        }
        expected = (
            None
            if params is None or params.cosmic_effect is None
            else cosmic_values[params.cosmic_effect]
        )
        if value != expected:
            raise ValidationError("Cosmic approval percentage does not match its named effect")
    params = selection.parameters
    if selection.definition_id == CONE and params is not None:
        expected = 50 + 10 * (params.width_yards or 0)
        if selection.option != f"width-{params.width_yards}" or value != expected:
            raise ValidationError("Cone approval does not match its authored width")
    if selection.definition_id == "modifier:enhancement:cyclic" and params is not None:
        intervals = {1: 100, 10: 50, 60: 40, 3600: 20, 86_400: 10}
        base = None if params.interval_seconds is None else intervals.get(params.interval_seconds)
        contagious = {"none": 0, "mild": 20, "high": 50}.get(params.contagious or "none", 0)
        expected = (
            None
            if base is None or params.cycles is None
            else base * (params.cycles - 1) + contagious
        )
        if value != expected:
            raise ValidationError("Cyclic approval does not match interval, cycles, and contagion")
    if selection.definition_id == "modifier:enhancement:delay" and params is not None:
        expected = (
            50
            if params.trigger is not None
            else 10
            if params.variable_delay_max_seconds is not None
            and params.variable_delay_max_seconds <= 10
            else 20
            if params.variable_delay_max_seconds is not None
            else 0
        )
        if value != expected:
            raise ValidationError("Delay approval does not match its authored mode")
    if selection.definition_id == "modifier:enhancement:hazard" and params is not None:
        hazard_values = {
            "dehydration": 20,
            "drowning": 0,
            "freezing": 20,
            "missed-sleep": 50,
            "starvation": 40,
            "suffocation": 0,
        }
        expected = None if params.hazard is None else hazard_values[params.hazard]
        if value != expected or selection.option != params.hazard:
            raise ValidationError("Hazard approval does not match its named effect")
    if definition.classification is ModifierClass.ENHANCEMENT and value < 0:
        raise ValidationError("Enhancement approval cannot reduce cost")
    if definition.classification is not ModifierClass.ENHANCEMENT and value > 0:
        raise ValidationError("Limitation approval cannot increase cost")
    return value


_COST_PERCENTAGES: Final[
    dict[
        CostKind,
        Callable[[ModifierSelection, ModifierDefinition, tuple[ModifierApproval, ...]], int],
    ]
] = {
    CostKind.FIXED: _fixed_percentage,
    CostKind.PER_LEVEL: _per_level_percentage,
    CostKind.TABLE: _table_percentage,
    CostKind.GADGET: _gadget_percentage,
    CostKind.CAMPAIGN_APPROVAL: _approved_percentage,
}


def _limited_percentage(
    selection: ModifierSelection,
    definition: ModifierDefinition,
    approvals: tuple[ModifierApproval, ...],
    value: int,
) -> int:
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
    value = _COST_PERCENTAGES[definition.cost_kind](selection, definition, approvals)
    params = selection.limitation
    if params is not None:
        if selection.definition_id == "modifier:limitation:onset" and params.exposure_time:
            value -= 20
        elif selection.definition_id == "modifier:limitation:limited-use":
            if params.limited_use_reload == "fast":
                value = int(Decimal(value) / 2)
            elif params.limited_use_reload == "slow":
                value += 5
        elif (
            selection.definition_id == "modifier:limitation:preparation-required"
            and params.preparation_weakened
        ):
            value = int(Decimal(value) / 2)
        elif selection.definition_id == REDUCED_RANGE and params.half_damage_range_only:
            value = int(Decimal(value) / 2)
    return _limited_percentage(selection, definition, approvals, value)


def _validate_modifier_definition(
    subject: AbilityKind, selection: ModifierSelection, selected: set[str]
) -> None:
    definition = MODIFIER_INDEX.get(selection.definition_id)
    if definition is None or subject not in definition.allowed_subjects:
        raise ValidationError("Modifier is unavailable for this ability kind")
    if not definition.selectable:
        raise ValidationError("Modifier catalog grouping is not directly selectable")
    if not set(definition.requires) <= selected:
        raise ValidationError("Modifier prerequisite is absent")
    if set(definition.excludes) & selected:
        raise ValidationError("Incompatible ability modifiers")


def _validate_unique_selection(
    selection: ModifierSelection,
    selections: tuple[ModifierSelection, ...],
    selected: set[str],
) -> None:
    if selection.definition_id != UNIQUE:
        return
    if not {BREAKABLE, STOLEN} & selected:
        raise ValidationError("Unique requires Breakable or Can Be Stolen")
    facts = tuple(
        candidate.gadget
        for candidate in selections
        if candidate.definition_id in {BREAKABLE, STOLEN} and candidate.gadget is not None
    )
    if not facts or not all(item.unique for item in facts):
        raise ValidationError("Unique requires gadget facts marked unique")


def _validate_spatial_relationships(
    subject: AbilityKind,
    selection: ModifierSelection,
    selections: tuple[ModifierSelection, ...],
    selected: set[str],
) -> None:
    if selection.definition_id == AURA:
        melee = next(candidate for candidate in selections if candidate.definition_id == MELEE)
        if melee.option != "reach-c":
            raise ValidationError("Aura requires Melee Attack at reach C")
    if selection.definition_id == "modifier:enhancement:drifting" and not selected & {
        "modifier:enhancement:delay",
        "modifier:enhancement:persistent",
    }:
        raise ValidationError("Drifting requires Delay or Persistent")
    if selection.definition_id == "modifier:enhancement:selective-area" and not selected & {
        AREA,
        CONE,
    }:
        raise ValidationError("Selective Area requires Area Effect or Cone")
    if selection.definition_id == "modifier:enhancement:extended-duration" and subject in _ATTACK:
        if not selected & {AURA, "modifier:enhancement:persistent", "modifier:enhancement:wall"}:
            raise ValidationError("Attack Extended Duration requires Aura, Persistent, or Wall")


def _validate_delivery_relationships(selection: ModifierSelection, selected: set[str]) -> None:
    if selection.definition_id in {
        "modifier:enhancement:blood-agent",
        "modifier:enhancement:contact-agent",
    } and not selected & {AREA, CONE}:
        raise ValidationError("Agent enhancement requires Area Effect or Cone")
    if selection.definition_id == "modifier:enhancement:respiratory-agent" and not selected & {
        AREA,
        CONE,
        JET,
    }:
        raise ValidationError("Respiratory Agent requires Area Effect, Cone, or Jet")
    if selection.definition_id == "modifier:enhancement:malediction":
        # Sense-Based is the explicit B109 exception: with Malediction it is
        # represented by the limitation row and remains a penetration adapter.
        conventional = set(_PENETRATION) - {
            "modifier:enhancement:sense-based",
            "modifier:limitation:sense-based",
        }
        if conventional & selected:
            raise ValidationError("Malediction is incompatible with this penetration modifier")
    params = selection.parameters
    if (
        selection.definition_id == "modifier:enhancement:cosmic"
        and params is not None
        and params.cosmic_effect == "irresistible-attack"
        and set(_PENETRATION) & selected
    ):
        raise ValidationError("Irresistible Cosmic attack cannot add penetration modifiers")
    if selection.definition_id == "modifier:limitation:onset" and not selected & {
        "modifier:limitation:blood-agent",
        "modifier:limitation:contact-agent",
        "modifier:enhancement:follow-up",
        "modifier:enhancement:malediction",
        "modifier:enhancement:respiratory-agent",
    }:
        raise ValidationError("Onset requires an eligible delivery modifier")
    if selection.definition_id == "modifier:limitation:resistible" and not selected & {
        "modifier:limitation:blood-agent",
        "modifier:limitation:contact-agent",
        "modifier:enhancement:follow-up",
        "modifier:enhancement:respiratory-agent",
        "modifier:limitation:sense-based",
    }:
        raise ValidationError("Resistible requires an eligible delivery modifier")
    if selection.definition_id == "modifier:limitation:extra-recoil" and RAPID_FIRE not in selected:
        raise ValidationError("Extra Recoil requires Rapid Fire")


def _validate_selectivity(selection: ModifierSelection, selected: set[str]) -> None:
    if selection.definition_id != "modifier:enhancement:selectivity":
        return
    disabled = set(selection.parameters.disabled_enhancements if selection.parameters else ())
    if (
        not disabled
        or not disabled < selected
        or any(
            MODIFIER_INDEX[item].classification is not ModifierClass.ENHANCEMENT
            for item in disabled
        )
    ):
        raise ValidationError("Selectivity must name selected enhancements it can disable")


def validate_selections(
    subject: AbilityKind,
    selections: tuple[ModifierSelection, ...],
    approvals: tuple[ModifierApproval, ...] = (),
) -> tuple[ModifierCost, ...]:
    identifiers = tuple(selection.definition_id for selection in selections)
    if len(identifiers) != len(set(identifiers)):
        raise ValidationError("Duplicate ability modifier")
    selected = set(identifiers)
    approval_index = {(approval.modifier_id, approval.option): approval for approval in approvals}
    if len(approval_index) != len(approvals):
        raise ValidationError("Duplicate campaign modifier approval")
    costs: list[ModifierCost] = []
    for selection in selections:
        _validate_modifier_definition(subject, selection, selected)
        _validate_unique_selection(selection, selections, selected)
        _validate_runtime_parameters(selection)
        _validate_spatial_relationships(subject, selection, selections, selected)
        _validate_delivery_relationships(selection, selected)
        _validate_selectivity(selection, selected)
        _validate_limitation_parameters(selection)
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


_PARAMETER_FIELDS: Final[dict[str, frozenset[str]]] = {
    CONE: frozenset({"width_yards"}),
    "modifier:enhancement:cosmic": frozenset({"cosmic_effect"}),
    "modifier:enhancement:cyclic": frozenset(
        {"interval_seconds", "cycles", "contagious", "stop_condition", "damage_kind"}
    ),
    "modifier:enhancement:delay": frozenset(
        {"delay_seconds", "variable_delay_max_seconds", "trigger"}
    ),
    "modifier:enhancement:follow-up": frozenset({"carrier_id"}),
    "modifier:enhancement:homing": frozenset({"homing_sense"}),
    "modifier:enhancement:link": frozenset({"linked_ability_id"}),
    RAPID_FIRE: frozenset({"rate_of_fire", "selective_fire"}),
    "modifier:enhancement:based-on-different-attribute": frozenset({"resistance_attribute"}),
    "modifier:enhancement:hazard": frozenset({"hazard", "damage_kind"}),
    "modifier:enhancement:radiation-rad": frozenset({"damage_kind"}),
    "modifier:enhancement:extended-duration": frozenset({"permanent_end_condition"}),
    "modifier:enhancement:selectivity": frozenset({"disabled_enhancements"}),
    "modifier:enhancement:symptoms": frozenset({"symptom", "symptom_threshold", "damage_kind"}),
    "modifier:enhancement:variable": frozenset({"damage_fraction"}),
}


_LIMITATION_PARAMETER_FIELDS: Final[dict[str, frozenset[str]]] = {
    "modifier:limitation:accessibility": frozenset({"condition_id"}),
    "modifier:limitation:always-on": frozenset({"always_on_severity"}),
    "modifier:limitation:bombardment": frozenset({"bombardment_skill"}),
    "modifier:limitation:limited-use": frozenset({"limited_use_reload"}),
    "modifier:limitation:mitigator": frozenset({"mitigator_id"}),
    "modifier:limitation:nuisance-effect": frozenset({"nuisance_effect_id"}),
    "modifier:limitation:onset": frozenset({"exposure_time"}),
    "modifier:limitation:pact": frozenset({"pact_id"}),
    "modifier:limitation:preparation-required": frozenset({"preparation_weakened"}),
    "modifier:limitation:reduced-range": frozenset({"half_damage_range_only"}),
    "modifier:limitation:resistible": frozenset({"resistance_modifier"}),
    "modifier:limitation:sense-based": frozenset({"sense"}),
    "modifier:limitation:temporary-disadvantage": frozenset({"temporary_disadvantage_ids"}),
    "modifier:limitation:trigger": frozenset({"trigger_id", "trigger_dangerous"}),
    "modifier:limitation:uncontrollable": frozenset({"uncontrollable_harmful"}),
}


def _validate_limitation_parameters(selection: ModifierSelection) -> None:
    params = selection.limitation
    provided = frozenset() if params is None else frozenset(params.model_fields_set)
    allowed = _LIMITATION_PARAMETER_FIELDS.get(selection.definition_id, frozenset())
    if provided - allowed:
        raise ValidationError("Limitation has unsupported runtime parameters")
    required = {
        "modifier:limitation:always-on": frozenset({"always_on_severity"}),
        "modifier:limitation:bombardment": frozenset({"bombardment_skill"}),
        "modifier:limitation:resistible": frozenset({"resistance_modifier"}),
        "modifier:limitation:sense-based": frozenset({"sense"}),
        "modifier:limitation:trigger": frozenset({"trigger_id"}),
    }
    if not required.get(selection.definition_id, frozenset()) <= provided:
        raise ValidationError("Limitation requires explicit runtime parameters")
    if params is None:
        return
    if (
        selection.definition_id == "modifier:limitation:always-on"
        and selection.option != params.always_on_severity
    ):
        raise ValidationError("Always On option must match its authored severity")
    if selection.definition_id == "modifier:limitation:bombardment" and selection.option != str(
        params.bombardment_skill
    ):
        raise ValidationError("Bombardment option must match its effective skill")
    if selection.definition_id == "modifier:limitation:resistible":
        expected = (
            None if params.resistance_modifier is None else f"ht{params.resistance_modifier:+d}"
        )
        if selection.option != expected:
            raise ValidationError("Resistible option must match its HT modifier")
    if selection.definition_id == "modifier:limitation:trigger":
        dangerous = bool(params.trigger_dangerous)
        if dangerous != bool(selection.option and selection.option.endswith("-dangerous")):
            raise ValidationError("Trigger option must match its dangerous status")
    if (
        selection.definition_id == "modifier:limitation:temporary-disadvantage"
        and not params.temporary_disadvantage_ids
    ):
        raise ValidationError("Temporary Disadvantage requires at least one exact trait")


def _provided_parameter_fields(parameters: EnhancementParameters | None) -> frozenset[str]:
    if parameters is None:
        return frozenset()
    return frozenset(parameters.model_fields_set)


def _validate_cosmic_parameters(
    selection: ModifierSelection, params: EnhancementParameters
) -> None:
    if (
        selection.definition_id == "modifier:enhancement:cosmic"
        and selection.option != params.cosmic_effect
    ):
        raise ValidationError("Cosmic approval must name the exact supported effect")


def _validate_cyclic_parameters(
    selection: ModifierSelection, params: EnhancementParameters
) -> None:
    if selection.definition_id != "modifier:enhancement:cyclic":
        return
    if params.interval_seconds not in {1, 10, 60, 3600, 86_400}:
        raise ValidationError("Cyclic interval is outside the Basic Set table")
    if params.damage_kind not in {"burning", "corrosion", "fatigue", "toxic"}:
        raise ValidationError("Cyclic requires burning, corrosion, fatigue, or toxic damage")
    if params.damage_kind in {"burning", "corrosion"} and params.interval_seconds > 10:
        raise ValidationError("Burning and corrosion cycles cannot exceed 10 seconds")


def _validate_timing_parameters(
    selection: ModifierSelection, params: EnhancementParameters
) -> None:
    if selection.definition_id == "modifier:enhancement:delay":
        modes = sum(
            value is not None
            for value in (params.delay_seconds, params.variable_delay_max_seconds, params.trigger)
        )
        if modes != 1:
            raise ValidationError("Delay requires exactly one fixed delay or named trigger")
    if (
        selection.definition_id == "modifier:enhancement:extended-duration"
        and selection.option == "permanent"
        and params.permanent_end_condition is None
    ):
        raise ValidationError("Permanent duration requires a named ending condition")


def _validate_damage_parameters(
    selection: ModifierSelection, params: EnhancementParameters
) -> None:
    if selection.definition_id == "modifier:enhancement:hazard" and params.damage_kind != "fatigue":
        raise ValidationError("Hazard is only available for fatigue damage")
    if selection.definition_id == "modifier:enhancement:radiation-rad":
        if params.damage_kind not in {"burning", "toxic"}:
            raise ValidationError("Radiation requires burning or toxic damage")
        if selection.option != params.damage_kind:
            raise ValidationError("Radiation cost option must match its damage type")


def _validate_rapid_fire_parameters(
    selection: ModifierSelection, params: EnhancementParameters
) -> None:
    if selection.definition_id != RAPID_FIRE:
        return
    rof = params.rate_of_fire or 0
    bands = {
        "rof-2": range(2, 3),
        "rof-3": range(3, 4),
        "rof-4-7": range(4, 8),
        "rof-8-15": range(8, 16),
        "rof-16-30": range(16, 31),
        "rof-31-70": range(31, 71),
        "rof-71-150": range(71, 151),
        "rof-151-300": range(151, 301),
    }
    if selection.option not in bands or rof not in bands[selection.option]:
        raise ValidationError("Rapid Fire rate does not match its Basic Set cost band")
    if params.selective_fire and rof < 5:
        raise ValidationError("Selective Fire requires Rate of Fire 5 or higher")


def _validate_runtime_parameters(selection: ModifierSelection) -> None:
    """Reject unnamed, irrelevant, or internally inconsistent runtime effects."""
    allowed = _PARAMETER_FIELDS.get(selection.definition_id, frozenset())
    provided = _provided_parameter_fields(selection.parameters)
    if provided - allowed:
        raise ValidationError("Enhancement has unsupported runtime parameters")
    required: dict[str, frozenset[str]] = {
        CONE: frozenset({"width_yards"}),
        "modifier:enhancement:cosmic": frozenset({"cosmic_effect"}),
        "modifier:enhancement:cyclic": frozenset(
            {"interval_seconds", "cycles", "stop_condition", "damage_kind"}
        ),
        "modifier:enhancement:follow-up": frozenset({"carrier_id"}),
        "modifier:enhancement:homing": frozenset({"homing_sense"}),
        "modifier:enhancement:link": frozenset({"linked_ability_id"}),
        RAPID_FIRE: frozenset({"rate_of_fire"}),
        "modifier:enhancement:based-on-different-attribute": frozenset({"resistance_attribute"}),
        "modifier:enhancement:hazard": frozenset({"hazard", "damage_kind"}),
        "modifier:enhancement:radiation-rad": frozenset({"damage_kind"}),
        "modifier:enhancement:symptoms": frozenset({"symptom", "symptom_threshold", "damage_kind"}),
        "modifier:enhancement:variable": frozenset({"damage_fraction"}),
    }
    if not required.get(selection.definition_id, frozenset()) <= provided:
        raise ValidationError("Enhancement requires explicit runtime parameters")
    params = selection.parameters
    if params is None:
        return
    _validate_cosmic_parameters(selection, params)
    _validate_cyclic_parameters(selection, params)
    _validate_timing_parameters(selection, params)
    _validate_damage_parameters(selection, params)
    _validate_rapid_fire_parameters(selection, params)


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


RuntimeAdapter = Callable[
    [AttackProfile, ModifierSelection, ModifierDefinition, EnhancementParameters], AttackProfile
]


def _accuracy_adapter(
    profile: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
    parameters: EnhancementParameters,
) -> AttackProfile:
    del parameters
    delta = selection.level * (1 if definition.classification is ModifierClass.ENHANCEMENT else -1)
    if profile.accuracy + delta < 0:
        raise ValidationError("Inaccurate cannot reduce Accuracy below zero")
    return profile.model_copy(update={"accuracy": profile.accuracy + delta})


def _range_adapter(
    profile: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
    parameters: EnhancementParameters,
) -> AttackProfile:
    del parameters
    multiplier = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)[selection.level]
    value = (
        profile.max_range * multiplier
        if definition.classification is ModifierClass.ENHANCEMENT
        else profile.max_range // multiplier
    )
    update = {"max_range": value}
    if definition.classification is ModifierClass.LIMITATION:
        params = selection.limitation
        if params is None or not params.half_damage_range_only:
            update["half_damage_range"] = profile.half_damage_range // multiplier
        else:
            update = {"half_damage_range": profile.half_damage_range // multiplier}
    return profile.model_copy(update=update)


def _limitation_profile_adapter(
    profile: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
    parameters: EnhancementParameters,
) -> AttackProfile:
    del definition, parameters
    params = selection.limitation or LimitationParameters()
    hook = MODIFIER_INDEX[selection.definition_id].runtime_hook
    if hook == "extra-recoil":
        return profile.model_copy(update={"recoil": min(5, profile.recoil + selection.level)})
    if hook == "melee":
        reaches = {
            "reach-c": "C",
            "reach-1-2": "1-2",
            "reach-variable": "C,1 or 1,2 or 2,3",
            "reach-1-4": "1-4",
        }
        return profile.model_copy(
            update={
                "is_ranged": False,
                "max_range": 0,
                "half_damage_range": 0,
                "accuracy": 0,
                "rate_of_fire": 1,
                "recoil": 1,
                "reach": reaches[selection.option or ""],
            }
        )
    if hook == "bombardment":
        return profile.model_copy(update={"bombardment_skill": params.bombardment_skill})
    if hook == "dissipation":
        return profile.model_copy(update={"dissipates_with_distance": True})
    if hook == "emanation":
        return profile.model_copy(
            update={"is_ranged": False, "max_range": 0, "half_damage_range": 0, "accuracy": 0}
        )
    if hook == "limited-use":
        uses = {"1-per-day": 1, "2-per-day": 2, "3-4-per-day": 4, "5-10-per-day": 10}
        return profile.model_copy(update={"uses_per_day": uses[selection.option or ""]})
    if hook == "onset":
        seconds = {
            "1-minute": 60,
            "1-hour": 3600,
            "1-day": 86400,
            "1-week": 604800,
        }[selection.option or ""]
        return profile.model_copy(
            update={
                "onset_seconds": seconds,
                "onset_requires_continuous_exposure": params.exposure_time,
            }
        )
    if hook == "preparation":
        seconds = {
            "1-minute": 60,
            "10-minutes": 600,
            "1-hour": 3600,
            "8-hours": 28800,
        }[selection.option or ""]
        return profile.model_copy(update={"preparation_seconds": seconds})
    if hook == "recharge":
        seconds = {"5-seconds": 5, "15-seconds": 15, "1-hour": 3600}[selection.option or ""]
        return profile.model_copy(update={"recharge_seconds": seconds})
    if hook == "resistible":
        return profile.model_copy(update={"resistance_modifier": params.resistance_modifier})
    if hook == "always-on":
        return profile.model_copy(update={"always_on": True})
    if hook == "untrainable":
        return profile.model_copy(update={"trained_control_allowed": False})
    return profile


def _duration_adapter(
    profile: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
    parameters: EnhancementParameters,
) -> AttackProfile:
    del definition
    multipliers = {
        "3x": 3,
        "10x": 10,
        "30x": 30,
        "100x": 100,
        "300x": 300,
        "1000x": 1000,
    }
    permanent = selection.option == "permanent"
    seconds = 0 if permanent else profile.duration_seconds * multipliers[selection.option or ""]
    return profile.model_copy(
        update={
            "duration_seconds": seconds,
            "duration_end_condition": parameters.permanent_end_condition if permanent else None,
        }
    )


def _penetration_adapter(
    profile: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
    parameters: EnhancementParameters,
) -> AttackProfile:
    del selection
    update: dict[str, object] = {"penetration_modifier": definition.runtime_hook}
    if definition.runtime_hook == "follow-up":
        update["carrier_id"] = parameters.carrier_id
    return profile.model_copy(update=update)


def _sense_adapter(
    profile: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
    parameters: EnhancementParameters,
) -> AttackProfile:
    del definition, parameters
    if not selection.option:
        raise ValidationError("Sense-Based requires an authored target sense")
    return profile.model_copy(
        update={"penetration_modifier": "sense-based", "penetration_sense": selection.option}
    )


def _activation_adapter(
    profile: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
    parameters: EnhancementParameters,
) -> AttackProfile:
    del parameters
    seconds = (
        max(0, profile.activation_seconds // (2**selection.level))
        if definition.classification is ModifierClass.ENHANCEMENT
        else profile.activation_seconds * (2**selection.level)
    )
    return profile.model_copy(update={"activation_seconds": seconds})


def _wall_adapter(
    profile: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
    parameters: EnhancementParameters,
) -> AttackProfile:
    del definition, parameters
    rigid = selection.option == "rigid"
    return profile.model_copy(
        update={
            "wall": selection.option,
            "wall_shape_flexible": selection.option == "permeable",
            "wall_dr_per_die": 3 if rigid else 0,
            "wall_hp_per_die": Decimal("0.5") if rigid else Decimal(0),
        }
    )


_STATIC_RUNTIME_UPDATES: Final[dict[str, dict[str, object]]] = {
    "affects-insubstantial": {"affects_insubstantial": True},
    "affects-substantial": {
        "affects_substantial": True,
        "affects_insubstantial": True,
        "usable_while_insubstantial": True,
    },
    "aura": {"aura": True, "is_ranged": False, "max_range": 0},
    "double-blunt-trauma": {"blunt_trauma_multiplier": 2},
    "double-knockback": {"knockback_multiplier": 2},
    "drifting": {"drifting": True},
    "guided": {"guidance": "guided"},
    "jet": {"jet": True, "accuracy": 0, "half_damage_range": 0, "rate_of_fire": 1},
    "low-signature": {"signature": "low"},
    "no-signature": {"signature": "none"},
    "overhead": {"overhead": True},
    "persistent": {"persistent": True, "duration_seconds": 10},
    "ranged": {
        "is_ranged": True,
        "half_damage_range": 10,
        "max_range": 100,
        "accuracy": 3,
        "rate_of_fire": 1,
        "duration_seconds": 10,
    },
    "selective-area": {"selective_area": True},
    "side-effect": {"affliction_delivery": "side-effect"},
    "surge": {"surge": True},
    "underwater": {"underwater_range_divisor": 10},
    "no-blunt-trauma": {"blunt_trauma_multiplier": 0},
    "no-knockback": {"knockback_multiplier": 0},
    "no-wounding": {"wounding": False},
}


_RUNTIME_ADAPTERS: Final[dict[str, RuntimeAdapter]] = {
    "accuracy": _accuracy_adapter,
    "range": _range_adapter,
    "area": lambda p, s, d, x: p.model_copy(update={"area_radius": 2**s.level}),
    "resistance-attribute": lambda p, s, d, x: p.model_copy(
        update={"resistance_attribute": x.resistance_attribute}
    ),
    "cone": lambda p, s, d, x: p.model_copy(update={"cone_width_yards": x.width_yards}),
    "cosmic": lambda p, s, d, x: p.model_copy(update={"cosmic_effect": x.cosmic_effect}),
    "cyclic": lambda p, s, d, x: p.model_copy(
        update={
            "cyclic_interval_seconds": x.interval_seconds,
            "cyclic_cycles": x.cycles,
            "cyclic_stop_condition": x.stop_condition,
            "contagious": x.contagious or "none",
        }
    ),
    "duration": _duration_adapter,
    "delay": lambda p, s, d, x: p.model_copy(
        update={
            "delay_seconds": x.delay_seconds or 0,
            "variable_delay_max_seconds": x.variable_delay_max_seconds,
            "delay_trigger": x.trigger,
        }
    ),
    "armor-divisor": lambda p, s, d, x: p.model_copy(
        update={
            "armor_divisor": Decimal(s.option or "1"),
            "penetration_modifier": "armor-divisor",
        }
    ),
    "blood-agent": _penetration_adapter,
    "contact-agent": _penetration_adapter,
    "respiratory-agent": _penetration_adapter,
    "follow-up": _penetration_adapter,
    "sense-based": _sense_adapter,
    "fatigue": lambda p, s, d, x: p.model_copy(
        update={"fatigue_cost": max(0, p.fatigue_cost - s.level)}
    ),
    "fatigue-cost": lambda p, s, d, x: p.model_copy(
        update={"fatigue_cost": p.fatigue_cost + s.level}
    ),
    "activation-time": _activation_adapter,
    "explosion": lambda p, s, d, x: p.model_copy(update={"explosion_divisor": 4 - s.level}),
    "fragmentation": lambda p, s, d, x: p.model_copy(update={"fragmentation_dice": s.level}),
    "homing": lambda p, s, d, x: p.model_copy(
        update={"guidance": "homing", "homing_sense": x.homing_sense}
    ),
    "hazard": lambda p, s, d, x: p.model_copy(update={"hazard": x.hazard}),
    "link": lambda p, s, d, x: p.model_copy(
        update={
            "linked_ability_id": x.linked_ability_id,
            "link_selectable": s.option == "selectable",
        }
    ),
    "malediction": lambda p, s, d, x: p.model_copy(
        update={
            "malediction_range": {
                "1": "yards",
                "2": "speed-range",
                "3": "long-distance",
            }[s.option or ""],
            "armor_divisor": Decimal("1000000"),
        }
    ),
    "mobile": lambda p, s, d, x: p.model_copy(update={"mobile_move": s.level}),
    "radiation": lambda p, s, d, x: p.model_copy(
        update={
            "radiation_mode": ("instead-of-damage" if x.damage_kind == "toxic" else "additional")
        }
    ),
    "rapid-fire": lambda p, s, d, x: p.model_copy(
        update={"rate_of_fire": x.rate_of_fire, "selective_fire": x.selective_fire}
    ),
    "selectivity": lambda p, s, d, x: p.model_copy(
        update={
            "switchable_enhancements": True,
            "disabled_enhancements": x.disabled_enhancements,
        }
    ),
    "symptoms": lambda p, s, d, x: p.model_copy(
        update={"symptom": x.symptom, "symptom_threshold": x.symptom_threshold}
    ),
    "variable": lambda p, s, d, x: p.model_copy(
        update={"variable_damage_fraction": x.damage_fraction}
    ),
    "wall": _wall_adapter,
    "bombardment": _limitation_profile_adapter,
    "dissipation": _limitation_profile_adapter,
    "emanation": _limitation_profile_adapter,
    "extra-recoil": _limitation_profile_adapter,
    "limited-use": _limitation_profile_adapter,
    "melee": _limitation_profile_adapter,
    "onset": _limitation_profile_adapter,
    "preparation": _limitation_profile_adapter,
    "recharge": _limitation_profile_adapter,
    "resistible": _limitation_profile_adapter,
    "always-on": _limitation_profile_adapter,
    "untrainable": _limitation_profile_adapter,
}


def _apply_attack_modifier(
    result: AttackProfile,
    selection: ModifierSelection,
    definition: ModifierDefinition,
) -> AttackProfile:
    hook = definition.runtime_hook or ""
    static_update = _STATIC_RUNTIME_UPDATES.get(hook)
    if static_update is not None:
        return result.model_copy(update=static_update)
    adapter = _RUNTIME_ADAPTERS.get(hook)
    if adapter is not None:
        parameters = selection.parameters or EnhancementParameters()
        return adapter(result, selection, definition, parameters)
    if hook == "incendiary":
        return result.model_copy(update={"damage_tags": result.damage_tags + (hook,)})
    return result


_RUNTIME_HOOK_ORDER: Final = (
    "affects-insubstantial",
    "affects-substantial",
    "ranged",
    "range",
    "underwater",
    "accuracy",
    "area",
    "cone",
    "aura",
    "persistent",
    "duration",
    "delay",
    "drifting",
    "mobile",
    "wall",
    "resistance-attribute",
    "malediction",
    "armor-divisor",
    "blood-agent",
    "contact-agent",
    "respiratory-agent",
    "follow-up",
    "sense-based",
    "cosmic",
    "double-blunt-trauma",
    "double-knockback",
    "explosion",
    "fragmentation",
    "incendiary",
    "radiation",
    "surge",
    "hazard",
    "cyclic",
    "symptoms",
    "side-effect",
    "guided",
    "homing",
    "jet",
    "overhead",
    "rapid-fire",
    "low-signature",
    "no-signature",
    "selective-area",
    "selectivity",
    "variable",
    "link",
    "fatigue",
    "fatigue-cost",
    "activation-time",
    "no-blunt-trauma",
    "no-knockback",
    "no-wounding",
    "bombardment",
    "dissipation",
    "emanation",
    "extra-recoil",
    "limited-use",
    "melee",
    "onset",
    "preparation",
    "recharge",
    "resistible",
    "always-on",
    "untrainable",
)
_RUNTIME_ORDER_INDEX: Final = {hook: index for index, hook in enumerate(_RUNTIME_HOOK_ORDER)}


def _runtime_order(selection: ModifierSelection) -> tuple[int, str]:
    definition = MODIFIER_INDEX[selection.definition_id]
    return (
        _RUNTIME_ORDER_INDEX.get(definition.runtime_hook or "", len(_RUNTIME_HOOK_ORDER)),
        definition.id,
    )


def _validate_profile_compatibility(
    profile: AttackProfile, selections: tuple[ModifierSelection, ...]
) -> None:
    selected = {selection.definition_id: selection for selection in selections}
    if "modifier:enhancement:double-blunt-trauma-dbt" in selected and profile.damage_kind not in {
        "burning",
        "corrosion",
        "cutting",
        "impaling",
        "piercing",
    }:
        raise ValidationError("Double Blunt Trauma is unavailable for this damage type")
    if "modifier:enhancement:double-knockback-dkb" in selected and profile.damage_kind not in {
        "crushing",
        "cutting",
    }:
        raise ValidationError("Double Knockback requires crushing or cutting damage")
    if "modifier:enhancement:explosion-exp" in selected and profile.damage_kind not in {
        "crushing",
        "burning",
    }:
        raise ValidationError("Explosion requires crushing or burning damage")
    if "modifier:enhancement:incendiary-inc" in selected and profile.damage_kind == "burning":
        raise ValidationError("A burning attack cannot add Incendiary")
    for identifier in (
        "modifier:enhancement:cyclic",
        "modifier:enhancement:hazard",
        "modifier:enhancement:radiation-rad",
        "modifier:enhancement:symptoms",
    ):
        selection = selected.get(identifier)
        if (
            selection is not None
            and selection.parameters is not None
            and selection.parameters.damage_kind != profile.damage_kind
        ):
            raise ValidationError("Enhancement damage type does not match the attack profile")
    wall = selected.get("modifier:enhancement:wall")
    if (
        wall is not None
        and wall.option == "rigid"
        and profile.damage_kind
        not in {
            "crushing",
            "cutting",
            "impaling",
            "piercing",
        }
    ):
        raise ValidationError("Rigid Wall requires physical damage")
    if "modifier:enhancement:reduced-fatigue-cost" in selected:
        level = selected["modifier:enhancement:reduced-fatigue-cost"].level
        if profile.fatigue_cost == 0 or level > profile.fatigue_cost:
            raise ValidationError("Reduced Fatigue Cost requires enough existing FP cost")
    if "modifier:enhancement:ranged" in selected and profile.is_ranged:
        raise ValidationError("Ranged cannot modify an ability that already has range")
    if "modifier:enhancement:malediction" in selected:
        forbidden = {
            ACCURATE,
            INCREASED_RANGE,
            "modifier:enhancement:guided",
            "modifier:enhancement:homing",
            "modifier:enhancement:overhead",
            RAPID_FIRE,
        }
        if forbidden & selected.keys():
            raise ValidationError("Malediction cannot use conventional ranged attack modifiers")


def apply_ability_modifiers(
    profile: AttackProfile,
    subject: AbilityKind,
    selections: tuple[ModifierSelection, ...],
    approvals: tuple[ModifierApproval, ...] = (),
) -> ModifierRuntimeReceipt:
    """Apply runtime consequences in a stable, source-independent phase order."""
    validate_selections(subject, selections, approvals)
    _validate_profile_compatibility(profile, selections)
    selectivity = next(
        (
            selection
            for selection in selections
            if selection.definition_id == "modifier:enhancement:selectivity"
        ),
        None,
    )
    disabled = frozenset(
        selectivity.parameters.disabled_enhancements
        if selectivity is not None and selectivity.parameters is not None
        else ()
    )
    # Selectivity changes which purchased enhancements execute, not their
    # construction cost.  Determine that execution set before phase ordering so
    # an early adapter (Accurate, Area Effect, etc.) cannot leak into the result.
    ordered = tuple(
        sorted(
            (selection for selection in selections if selection.definition_id not in disabled),
            key=_runtime_order,
        )
    )
    result = profile
    for selection in ordered:
        definition = MODIFIER_INDEX[selection.definition_id]
        if definition.runtime_hook == "fatigue" and result.fatigue_cost == 0:
            raise ValidationError("Reduced Fatigue Cost requires an ability with an FP cost")
        result = _apply_attack_modifier(result, selection, definition)
    return ModifierRuntimeReceipt(
        original=profile,
        modified=result,
        applied_modifier_ids=tuple(selection.definition_id for selection in ordered),
    )


def apply_attack_modifiers(
    profile: AttackProfile,
    subject: AbilityKind,
    selections: tuple[ModifierSelection, ...],
    approvals: tuple[ModifierApproval, ...] = (),
) -> ModifierRuntimeReceipt:
    """Project selected executable modifiers into authoritative attack facts."""
    if subject not in _ATTACK:
        raise ValidationError("Attack modifier projection requires an attack ability")
    return apply_ability_modifiers(profile, subject, selections, approvals)


@dataclass(slots=True)
class _LimitationResolution:
    available: bool = True
    controlled: bool = True
    power: Decimal = Decimal(1)
    checks: tuple[CheckTrace, ...] = ()
    effects: tuple[str, ...] = ()
    temporary: tuple[str, ...] = ()


def _resolve_access_constraint(
    resolution: _LimitationResolution,
    selection: ModifierSelection,
    hook: str | None,
    projected: AttackProfile,
    context: LimitationContext,
) -> None:
    params = selection.limitation
    if hook == "accessibility":
        if params is None or params.condition_id is None:
            raise ValidationError("Accessibility execution requires an authored condition")
        resolution.available &= params.condition_id in context.satisfied_condition_ids
    elif hook == "emergencies-only":
        resolution.available &= context.emergency
    elif hook == "emergency-power" and not context.emergency:
        resolution.power = min(resolution.power, Decimal("0.5"))
    elif hook == "limited-use":
        resolution.available &= context.uses_today < (projected.uses_per_day or 0)
    elif hook == "mitigator":
        if params is None or params.mitigator_id is None:
            raise ValidationError("Mitigator execution requires an authoritative item")
        resolution.available &= params.mitigator_id not in context.available_mitigator_ids
    elif hook == "nuisance":
        if params is None or params.nuisance_effect_id is None:
            raise ValidationError("Nuisance Effect requires an executable effect adapter")
        resolution.effects += (params.nuisance_effect_id,)
    elif hook == "pact":
        if params is None or params.pact_id is None:
            raise ValidationError("Pact execution requires an authored code")
        resolution.available &= params.pact_id in context.observed_pact_ids


def _resolve_time_constraint(
    resolution: _LimitationResolution,
    selection: ModifierSelection,
    hook: str | None,
    profile: AttackProfile,
    projected: AttackProfile,
    context: LimitationContext,
) -> None:
    params = selection.limitation
    if hook == "preparation":
        prepared = (
            context.prepared_ability_id == context.ability_id
            and context.prepared_at is not None
            and context.prepared_at + projected.preparation_seconds <= context.now
        )
        if not prepared and params is not None and params.preparation_weakened:
            resolution.power = min(resolution.power, Decimal("0.5"))
        else:
            resolution.available &= prepared
    elif hook == "recharge" and context.last_used_at is not None:
        multiplier = {"5-seconds": 2, "15-seconds": 5, "1-hour": 10}[selection.option or ""]
        recharge = max(projected.recharge_seconds, profile.activation_seconds * multiplier)
        resolution.available &= context.now >= context.last_used_at + recharge
    elif hook == "temporary-disadvantage":
        if params is None or not params.temporary_disadvantage_ids:
            raise ValidationError("Temporary Disadvantage requires exact trait adapters")
        resolution.temporary += params.temporary_disadvantage_ids
    elif hook == "trigger":
        if params is None or params.trigger_id is None:
            raise ValidationError("Trigger execution requires an authored trigger")
        resolution.available &= params.trigger_id in context.supplied_trigger_ids
    elif hook == "unconscious-only":
        resolution.available &= not context.conscious


def _resolve_control_constraint(
    resolution: _LimitationResolution,
    selection: ModifierSelection,
    hook: str | None,
    selections: tuple[ModifierSelection, ...],
    context: LimitationContext,
    rng: RandomSource,
) -> None:
    if hook == "uncontrollable" and context.emergency:
        trace = success_check(
            context.will,
            rng=rng,
            rules_package=PROFILE,
            rules_version="characters-third-2008",
        )
        resolution.checks += (trace,)
        resolution.controlled &= trace.outcome.succeeded
    elif hook == "unreliable":
        option = selection.option or ""
        if option.startswith("activation-"):
            trace = success_check(
                int(option.removeprefix("activation-")),
                rng=rng,
                rules_package=PROFILE,
                rules_version="characters-third-2008",
            )
            resolution.checks += (trace,)
            resolution.available &= trace.outcome.succeeded
    elif hook == "gadget":
        if context.gadget is None:
            raise ValidationError("Gadget execution requires authoritative equipment state")
        resolution.available &= gadget_available(selections, context.gadget)


def resolve_limitations(
    profile: AttackProfile,
    subject: AbilityKind,
    selections: tuple[ModifierSelection, ...],
    context: LimitationContext,
    approvals: tuple[ModifierApproval, ...] = (),
    *,
    rng: RandomSource = NO_RANDOM,
) -> LimitationRuntimeReceipt:
    """Resolve one use against authored facts; random checks are retained for replay."""
    projected = apply_ability_modifiers(profile, subject, selections, approvals).modified
    resolution = _LimitationResolution()
    selected_ids = tuple(selection.definition_id for selection in selections)

    for selection in sorted(selections, key=_runtime_order):
        definition = MODIFIER_INDEX[selection.definition_id]
        if definition.classification is ModifierClass.ENHANCEMENT:
            continue
        hook = definition.runtime_hook
        _resolve_access_constraint(resolution, selection, hook, projected, context)
        _resolve_time_constraint(resolution, selection, hook, profile, projected, context)
        _resolve_control_constraint(resolution, selection, hook, selections, context, rng)

    fatigue = projected.fatigue_cost
    resolution.available &= context.fp_available >= fatigue
    next_available = (
        None
        if projected.recharge_seconds == 0
        else context.now
        + max(
            projected.recharge_seconds,
            profile.activation_seconds * 2,
        )
    )
    return LimitationRuntimeReceipt(
        actor_id=context.actor_id,
        ability_id=context.ability_id,
        at=context.now,
        available=resolution.available,
        controlled=resolution.controlled,
        power_fraction=resolution.power,
        fatigue_cost=fatigue,
        next_available_at=next_available,
        temporary_disadvantage_ids=resolution.temporary,
        runtime_effect_ids=resolution.effects,
        checks=resolution.checks,
        applied_modifier_ids=selected_ids,
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
