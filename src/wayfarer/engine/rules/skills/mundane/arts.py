"""Arts, crafts and trade procedures from the Basic Set skill chapter (#338)."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy as P
from wayfarer.engine.rules.skills.mundane.procedures import (
    Resolution as R,
)
from wayfarer.engine.rules.skills.mundane.procedures import (
    Task,
    bind,
)

OWNER: Final = 338
NONCOMBAT: Final = "noncombat.approach"
REPAIR: Final = "object.repair"
HAZARD: Final = "hazard.exposure"
SOCIAL: Final = "social.skill-procedure"


def task(
    dispatch: str,
    effect: str,
    unit: str,
    *,
    policy: P = P.RETRY_UNTIL_SUCCESS,
    resolution: R = R.SUCCESS_ROLL,
    context: tuple[str, ...] = (),
    modifiers: frozenset[str] = frozenset(),
    margin: bool = False,
    cap: int = 0,
) -> Task:
    return Task(
        dispatch,
        effect,
        policy,
        unit,
        resolution,
        context,
        modifiers,
        int(margin),
        cap,
    )


CRAFT: Final = frozenset({"equipment-quality", "time-spent", "workshop"})
PERFORMANCE: Final = frozenset({"audience", "equipment-quality", "preparation"})
CONCEALMENT: Final = frozenset({"item-size", "searcher-alertness", "preparation"})
KNOWLEDGE: Final = frozenset({"reference-quality", "time-spent"})
TRADE: Final = frozenset({"market", "reaction", "goods-quality"})
DEXTERITY: Final = frozenset({"item-size", "distraction", "equipment-quality"})

# Every value is an item-specific action contract.  Shared modifier sets name
# reusable rule axes; the effect and unit remain unique to the source entry.
_ROWS: Final = (
    ("skill:armoury", "Armoury", None),
    (
        "skill:armoury-battlesuits",
        "Armoury (Battlesuits)",
        task(
            REPAIR,
            "restore-battlesuit",
            "restored-hp",
            context=("matching-specialty",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:armoury-body-armor",
        "Armoury (Body Armor)",
        task(
            REPAIR,
            "restore-body-armor",
            "restored-hp",
            context=("matching-specialty",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:armoury-force-shields",
        "Armoury (Force Shields)",
        task(
            REPAIR,
            "restore-force-shield",
            "restored-hp",
            context=("matching-specialty",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:armoury-heavy-weapons",
        "Armoury (Heavy Weapons)",
        task(
            REPAIR,
            "restore-heavy-weapon",
            "restored-hp",
            context=("matching-specialty",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:armoury-melee-weapons",
        "Armoury (Melee Weapons)",
        task(
            REPAIR,
            "restore-melee-weapon",
            "restored-hp",
            context=("matching-specialty",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:armoury-missile-weapons",
        "Armoury (Missile Weapons)",
        task(
            REPAIR,
            "restore-missile-weapon",
            "restored-hp",
            context=("matching-specialty",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:armoury-small-arms",
        "Armoury (Small Arms)",
        task(
            REPAIR,
            "restore-small-arm",
            "restored-hp",
            context=("matching-specialty",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:armoury-vehicular-armor",
        "Armoury (Vehicular Armor)",
        task(
            REPAIR,
            "restore-vehicular-armor",
            "restored-hp",
            context=("matching-specialty",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:artist",
        "Artist",
        task(
            NONCOMBAT,
            "create-artwork",
            "quality-step",
            context=("materials-present",),
            modifiers=CRAFT,
            margin=True,
            cap=6,
        ),
    ),
    (
        "skill:bartender",
        "Bartender",
        task(
            SOCIAL,
            "serve-and-host",
            "reaction-step",
            context=("patrons-present",),
            modifiers=TRADE,
            margin=True,
            cap=3,
        ),
    ),
    (
        "skill:camouflage",
        "Camouflage",
        task(
            NONCOMBAT,
            "conceal-position",
            "concealment-margin",
            policy=P.UNKNOWN_UNTIL_LATER,
            resolution=R.QUICK_CONTEST,
            context=("terrain-described",),
            modifiers=CONCEALMENT,
            margin=True,
        ),
    ),
    (
        "skill:carpentry",
        "Carpentry",
        task(
            REPAIR,
            "construct-woodwork",
            "work-step",
            context=("tools-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:connoisseur",
        "Connoisseur",
        task(
            NONCOMBAT,
            "appraise-art-or-luxury",
            "finding",
            policy=P.SINGLE_CHANCE,
            context=("object-present",),
            modifiers=KNOWLEDGE,
            margin=True,
            cap=3,
        ),
    ),
    (
        "skill:cooking",
        "Cooking",
        task(NONCOMBAT, "prepare-meal", "meal", context=("ingredients-present",), modifiers=CRAFT),
    ),
    (
        "skill:counterfeiting",
        "Counterfeiting",
        task(
            NONCOMBAT,
            "produce-counterfeit",
            "quality-step",
            policy=P.UNKNOWN_UNTIL_LATER,
            resolution=R.QUICK_CONTEST,
            context=("sample-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:dancing",
        "Dancing",
        task(
            SOCIAL,
            "perform-dance",
            "audience-response",
            context=("performance-space",),
            modifiers=PERFORMANCE,
            margin=True,
            cap=3,
        ),
    ),
    (
        "skill:farming",
        "Farming",
        task(
            NONCOMBAT,
            "cultivate-crop",
            "yield-step",
            context=("land-and-season",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:filch",
        "Filch",
        task(
            NONCOMBAT,
            "snatch-visible-item",
            "item-taken",
            policy=P.HAZARDOUS_FAILURE,
            resolution=R.QUICK_CONTEST,
            context=("item-within-reach",),
            modifiers=DEXTERITY,
        ),
    ),
    (
        "skill:fire-eating",
        "Fire-Eating",
        task(
            HAZARD,
            "perform-fire-eating",
            "performance",
            policy=P.HAZARDOUS_FAILURE,
            context=("flame-present",),
            modifiers=PERFORMANCE,
        ),
    ),
    (
        "skill:forgery",
        "Forgery",
        task(
            NONCOMBAT,
            "produce-forgery",
            "quality-step",
            policy=P.UNKNOWN_UNTIL_LATER,
            resolution=R.QUICK_CONTEST,
            context=("sample-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:games",
        "Games",
        task(
            NONCOMBAT,
            "apply-game-rules",
            "game-advantage",
            resolution=R.QUICK_CONTEST,
            context=("game-selected",),
            modifiers=KNOWLEDGE,
            margin=True,
        ),
    ),
    ("skill:group-performance", "Group Performance", None),
    (
        "skill:group-performance-choreography",
        "Group Performance (Choreography)",
        task(
            SOCIAL,
            "coordinate-choreography",
            "group-step",
            context=("performers-present",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:group-performance-conducting",
        "Group Performance (Conducting)",
        task(
            SOCIAL,
            "conduct-ensemble",
            "group-step",
            context=("performers-present",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:group-performance-directing",
        "Group Performance (Directing)",
        task(
            SOCIAL,
            "direct-production",
            "group-step",
            context=("performers-present",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:group-performance-fight-choreography",
        "Group Performance (Fight Choreography)",
        task(
            SOCIAL,
            "coordinate-stage-fight",
            "group-step",
            context=("performers-present",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:heraldry",
        "Heraldry",
        task(
            NONCOMBAT,
            "identify-device-or-custom",
            "finding",
            policy=P.SINGLE_CHANCE,
            context=("device-or-custom-present",),
            modifiers=KNOWLEDGE,
            margin=True,
            cap=3,
        ),
    ),
    (
        "skill:hobby-skill",
        "Hobby Skill",
        task(
            NONCOMBAT,
            "apply-selected-hobby",
            "task-step",
            context=("subject-selected",),
            modifiers=KNOWLEDGE,
            margin=True,
        ),
    ),
    (
        "skill:holdout",
        "Holdout",
        task(
            NONCOMBAT,
            "conceal-carried-item",
            "concealment-margin",
            policy=P.UNKNOWN_UNTIL_LATER,
            resolution=R.QUICK_CONTEST,
            context=("item-present",),
            modifiers=CONCEALMENT,
            margin=True,
        ),
    ),
    (
        "skill:housekeeping",
        "Housekeeping",
        task(
            NONCOMBAT,
            "maintain-household",
            "household-step",
            context=("household-present",),
            modifiers=frozenset({"equipment-quality", "time-spent"}),
        ),
    ),
    (
        "skill:impersonate",
        "Impersonate",
        task(
            SOCIAL,
            "impersonate-known-person",
            "deception-margin",
            resolution=R.QUICK_CONTEST,
            context=("identity-selected",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:jeweler",
        "Jeweler",
        task(
            REPAIR,
            "make-or-repair-jewelry",
            "work-step",
            context=("tools-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:leatherworking",
        "Leatherworking",
        task(
            REPAIR,
            "make-or-repair-leatherwork",
            "work-step",
            context=("tools-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:lockpicking",
        "Lockpicking",
        task(
            NONCOMBAT,
            "open-lock",
            "lock-opened",
            policy=P.HAZARDOUS_FAILURE,
            context=("lock-and-tools",),
            modifiers=DEXTERITY,
        ),
    ),
    (
        "skill:machinist",
        "Machinist",
        task(
            REPAIR,
            "machine-part",
            "work-step",
            context=("machine-tools",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:makeup",
        "Makeup",
        task(
            SOCIAL,
            "alter-appearance",
            "disguise-step",
            resolution=R.QUICK_CONTEST,
            context=("subject-present",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:masonry",
        "Masonry",
        task(
            REPAIR,
            "construct-masonry",
            "work-step",
            context=("tools-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:merchant",
        "Merchant",
        task(
            SOCIAL,
            "negotiate-price",
            "price-step",
            resolution=R.QUICK_CONTEST,
            context=("trade-offer",),
            modifiers=TRADE,
            margin=True,
        ),
    ),
    ("skill:mimicry", "Mimicry", None),
    (
        "skill:mimicry-animal-sounds",
        "Mimicry (Animal Sounds)",
        task(
            SOCIAL,
            "imitate-animal",
            "deception-margin",
            resolution=R.QUICK_CONTEST,
            context=("sound-selected",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:mimicry-bird-calls",
        "Mimicry (Bird Calls)",
        task(
            SOCIAL,
            "imitate-bird",
            "deception-margin",
            resolution=R.QUICK_CONTEST,
            context=("sound-selected",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:mimicry-speech",
        "Mimicry (Speech)",
        task(
            SOCIAL,
            "imitate-speech",
            "deception-margin",
            resolution=R.QUICK_CONTEST,
            context=("voice-sample",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:musical-composition",
        "Musical Composition",
        task(
            NONCOMBAT,
            "compose-music",
            "quality-step",
            context=("composition-brief",),
            modifiers=CRAFT,
            margin=True,
            cap=6,
        ),
    ),
    (
        "skill:musical-instrument",
        "Musical Instrument",
        task(
            SOCIAL,
            "perform-instrument",
            "audience-response",
            context=("instrument-present",),
            modifiers=PERFORMANCE,
            margin=True,
            cap=3,
        ),
    ),
    (
        "skill:photography",
        "Photography",
        task(
            NONCOMBAT,
            "make-photograph",
            "quality-step",
            context=("camera-present",),
            modifiers=CRAFT,
            margin=True,
            cap=6,
        ),
    ),
    (
        "skill:motion-picture-camera",
        "Motion-Picture Camera",
        task(
            NONCOMBAT,
            "operate-motion-picture-camera",
            "quality-step",
            context=("camera-present",),
            modifiers=CRAFT,
            margin=True,
            cap=6,
        ),
    ),
    (
        "skill:pickpocket",
        "Pickpocket",
        task(
            NONCOMBAT,
            "steal-carried-item",
            "item-taken",
            policy=P.HAZARDOUS_FAILURE,
            resolution=R.QUICK_CONTEST,
            context=("item-accessible",),
            modifiers=DEXTERITY,
        ),
    ),
    (
        "skill:poetry",
        "Poetry",
        task(
            NONCOMBAT,
            "compose-poem",
            "quality-step",
            context=("composition-brief",),
            modifiers=CRAFT,
            margin=True,
            cap=6,
        ),
    ),
    (
        "skill:professional-skill",
        "Professional Skill",
        task(
            NONCOMBAT,
            "apply-selected-profession",
            "task-step",
            context=("subject-selected",),
            modifiers=KNOWLEDGE,
            margin=True,
        ),
    ),
    (
        "skill:prospecting",
        "Prospecting",
        task(
            NONCOMBAT,
            "locate-valuable-material",
            "finding",
            policy=P.UNKNOWN_UNTIL_LATER,
            context=("terrain-described",),
            modifiers=KNOWLEDGE,
            margin=True,
            cap=3,
        ),
    ),
    (
        "skill:sewing",
        "Sewing",
        task(
            REPAIR,
            "make-or-repair-textile",
            "work-step",
            context=("tools-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:singing",
        "Singing",
        task(
            SOCIAL,
            "perform-song",
            "audience-response",
            context=("audience-present",),
            modifiers=PERFORMANCE,
            margin=True,
            cap=3,
        ),
    ),
    (
        "skill:sleight-of-hand",
        "Sleight of Hand",
        task(
            NONCOMBAT,
            "perform-hand-trick",
            "deception-margin",
            resolution=R.QUICK_CONTEST,
            context=("observer-present",),
            modifiers=DEXTERITY,
            margin=True,
        ),
    ),
    ("skill:smith", "Smith", None),
    (
        "skill:smith-copper",
        "Smith (Copper)",
        task(
            REPAIR,
            "forge-copper",
            "work-step",
            context=("forge-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:smith-iron",
        "Smith (Iron)",
        task(
            REPAIR,
            "forge-iron",
            "work-step",
            context=("forge-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:smith-lead-and-tin",
        "Smith (Lead and Tin)",
        task(
            REPAIR,
            "forge-lead-or-tin",
            "work-step",
            context=("forge-present",),
            modifiers=CRAFT,
            margin=True,
        ),
    ),
    (
        "skill:smuggling",
        "Smuggling",
        task(
            NONCOMBAT,
            "conceal-shipment",
            "concealment-margin",
            policy=P.UNKNOWN_UNTIL_LATER,
            resolution=R.QUICK_CONTEST,
            context=("shipment-present",),
            modifiers=CONCEALMENT,
            margin=True,
        ),
    ),
    (
        "skill:typing",
        "Typing",
        task(
            NONCOMBAT,
            "produce-typed-copy",
            "copy-step",
            context=("keyboard-present",),
            modifiers=frozenset({"equipment-quality", "time-spent"}),
            margin=True,
        ),
    ),
    (
        "skill:ventriloquism",
        "Ventriloquism",
        task(
            SOCIAL,
            "throw-voice",
            "deception-margin",
            resolution=R.QUICK_CONTEST,
            context=("listener-present",),
            modifiers=PERFORMANCE,
            margin=True,
        ),
    ),
    (
        "skill:writing",
        "Writing",
        task(
            NONCOMBAT,
            "compose-text",
            "quality-step",
            context=("composition-brief",),
            modifiers=CRAFT,
            margin=True,
            cap=6,
        ),
    ),
)

PROCEDURES: Final = MappingProxyType(
    {identifier: bind(identifier, name, OWNER, selected) for identifier, name, selected in _ROWS}
)


def definitions() -> tuple[RuleDefinition, ...]:
    """Return concrete definitions without manufacturing family definitions."""
    return tuple(
        procedure.definition()
        for procedure in PROCEDURES.values()
        if procedure.dispatchable and procedure.spec() is not None
    )
