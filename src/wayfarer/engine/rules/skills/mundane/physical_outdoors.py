"""Physical, outdoor and animal skill procedures (#343)."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy as P
from wayfarer.engine.rules.skills.mundane.procedures import Resolution as R
from wayfarer.engine.rules.skills.mundane.procedures import Task, bind

OWNER: Final = 343
PHYSICAL: Final = "movement.physical-procedure"
TRAVEL: Final = "movement.approach"
MOUNT: Final = "movement.mount-operation"
HAZARD: Final = "hazard.exposure"
KNOWLEDGE: Final = "campaign.knowledge"
INVESTIGATION: Final = "noncombat.investigation"
SOCIAL: Final = "social.skill-procedure"
RANGED: Final = "combat.ranged-attack"


def task(
    dispatch: str,
    effect: str,
    unit: str,
    *,
    resolution: R = R.SUCCESS_ROLL,
    policy: P = P.RETRY_UNTIL_SUCCESS,
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


BODY = frozenset({"encumbrance", "surface", "speed", "injury", "time-pressure"})
ROUTE = frozenset({"terrain", "weather", "visibility", "equipment-quality", "time-spent"})
WILD = frozenset({"terrain", "weather", "season", "equipment-quality", "time-spent"})
ANIMAL = frozenset({"familiarity", "animal-temperament", "equipment-quality", "time-spent"})
SEARCH = frozenset({"terrain", "visibility", "evidence", "time-spent"})


def feat(effect: str, *context: str, modifiers: frozenset[str] = BODY) -> Task:
    return task(
        PHYSICAL,
        effect,
        "physical-progress",
        context=context,
        modifiers=modifiers,
        margin=True,
        cap=6,
    )


def travel(effect: str, *context: str) -> Task:
    return task(
        TRAVEL,
        effect,
        "route-progress",
        context=context,
        modifiers=ROUTE,
        margin=True,
        cap=6,
    )


def hazard(effect: str, *context: str) -> Task:
    return task(
        HAZARD,
        effect,
        "safe-exposure-step",
        context=context,
        modifiers=WILD,
        margin=True,
        cap=6,
    )


def knowledge(effect: str, *context: str) -> Task:
    return task(
        KNOWLEDGE,
        effect,
        "field-finding",
        policy=P.UNKNOWN_UNTIL_LATER,
        context=context,
        modifiers=WILD,
        margin=True,
        cap=5,
    )


def animal(effect: str, *context: str) -> Task:
    return task(
        MOUNT,
        effect,
        "animal-handling-step",
        context=context,
        modifiers=ANIMAL,
        margin=True,
        cap=5,
    )


_CORE_ROWS: Final = (
    ("skill:acrobatics", "Acrobatics", feat("perform-acrobatic-maneuver", "maneuver-space")),
    ("skill:aerobatics", "Aerobatics", feat("perform-aerial-maneuver", "free-flight")),
    (
        "skill:animal-handling",
        "Animal Handling",
        animal("handle-or-train-animal", "animal-present"),
    ),
    ("skill:aquabatics", "Aquabatics", feat("perform-underwater-maneuver", "underwater")),
    ("skill:bicycling", "Bicycling", travel("control-bicycle", "bicycle-and-route")),
    ("skill:body-sense", "Body Sense", feat("recover-spatial-orientation", "disorientation")),
    (
        "skill:breath-control",
        "Breath Control",
        hazard("manage-controlled-breathing", "breathing-task"),
    ),
    ("skill:climbing", "Climbing", feat("scale-surface", "climb-route")),
    (
        "skill:dropping",
        "Dropping",
        task(
            RANGED,
            "place-dropped-object",
            "attack-margin",
            context=("target-below",),
            modifiers=BODY,
            margin=True,
        ),
    ),
    ("skill:escape", "Escape", feat("escape-restraint", "restraint")),
    ("skill:falconry", "Falconry", animal("handle-or-train-raptor", "raptor-present")),
    ("skill:fishing", "Fishing", hazard("catch-fish", "fishable-water")),
    ("skill:flight", "Flight", travel("control-personal-flight", "air-route")),
    ("skill:forced-entry", "Forced Entry", feat("breach-obstacle", "barrier")),
    ("skill:free-fall", "Free Fall", hazard("maneuver-in-free-fall", "microgravity")),
    ("skill:gardening", "Gardening", knowledge("cultivate-plot", "garden-plot")),
    ("skill:hiking", "Hiking", travel("complete-overland-march", "overland-route")),
    ("skill:jumping", "Jumping", feat("clear-jump", "jump-route")),
    ("skill:knot-tying", "Knot-Tying", feat("secure-knot", "rope-and-load")),
    ("skill:lifesaving", "Lifesaving", hazard("rescue-swimmer", "swimmer-in-danger")),
    ("skill:lifting", "Lifting", feat("raise-or-shift-load", "load")),
    ("skill:meteorology", "Meteorology", None),
    ("skill:mount", "Mount", animal("mount-or-dismount", "mount-present")),
    ("skill:naturalist", "Naturalist", knowledge("interpret-ecosystem", "field-evidence")),
    ("skill:navigation", "Navigation", None),
    ("skill:packing", "Packing", animal("balance-pack-animal-load", "pack-animal-and-load")),
    (
        "skill:parachuting",
        "Parachuting",
        hazard("complete-parachute-descent", "parachute-and-drop-zone"),
    ),
    ("skill:riding", "Riding", animal("control-riding-animal", "riding-animal")),
    ("skill:rope-up", "Rope Up", feat("ascend-rope-quickly", "rope-climb")),
    ("skill:running", "Running", travel("sustain-distance-run", "running-route")),
    ("skill:scaling", "Scaling", feat("scale-route-quickly", "climb-route")),
    (
        "skill:scrounging",
        "Scrounging",
        task(
            INVESTIGATION,
            "find-useful-supplies",
            "found-supply",
            policy=P.UNKNOWN_UNTIL_LATER,
            context=("search-area",),
            modifiers=SEARCH,
            margin=True,
            cap=5,
        ),
    ),
    ("skill:skating", "Skating", travel("traverse-skating-route", "skates-and-route")),
    ("skill:skiing", "Skiing", travel("traverse-ski-route", "skis-and-route")),
    ("skill:slip-handcuffs", "Slip Handcuffs", feat("slip-handcuffs", "handcuffs")),
    (
        "skill:sports",
        "Sports",
        task(
            SOCIAL,
            "compete-in-sport",
            "contest-margin",
            resolution=R.QUICK_CONTEST,
            context=("sporting-contest",),
            modifiers=BODY,
            margin=True,
        ),
    ),
    ("skill:survival", "Survival", None),
    ("skill:swimming", "Swimming", travel("swim-route", "water-route")),
    ("skill:teamster", "Teamster", animal("drive-animal-team", "draft-team-and-route")),
    (
        "skill:throwing",
        "Throwing",
        task(
            RANGED,
            "throw-object-at-target",
            "attack-margin",
            context=("throwable-object-and-target",),
            modifiers=BODY,
            margin=True,
        ),
    ),
    (
        "skill:tracking",
        "Tracking",
        task(
            INVESTIGATION,
            "follow-trail",
            "trail-progress",
            policy=P.UNKNOWN_UNTIL_LATER,
            context=("trail-evidence",),
            modifiers=SEARCH,
            margin=True,
            cap=6,
        ),
    ),
    ("skill:urban-survival", "Urban Survival", hazard("find-urban-necessities", "urban-area")),
    (
        "skill:weather-sense",
        "Weather Sense",
        knowledge("forecast-local-weather", "local-weather-signs"),
    ),
)

_METEOROLOGY: Final = (
    "earthlike",
    "gas-giants",
    "hostile-terrestrial",
    "ice-dwarfs",
    "ice-worlds",
    "rock-worlds",
)
_NAVIGATION: Final = ("air", "land", "sea", "space", "hyperspace")
_SURVIVAL: Final = (
    "arctic",
    "desert",
    "island-beach",
    "jungle",
    "mountain",
    "plains",
    "swampland",
    "woodlands",
    "bank",
    "deep-ocean-vent",
    "fresh-water-lake",
    "open-ocean",
    "reef",
    "river-stream",
    "salt-water-sea",
    "tropical-lagoon",
)

_ROWS: Final = (
    _CORE_ROWS
    + tuple(
        (
            f"skill:meteorology-{name}",
            f"Meteorology ({name.replace('-', ' ').title()})",
            knowledge(f"forecast-{name}-weather", "weather-data"),
        )
        for name in _METEOROLOGY
    )
    + tuple(
        (
            f"skill:navigation-{name}",
            f"Navigation ({name.title()})",
            travel(f"plot-{name}-route", "route-data"),
        )
        for name in _NAVIGATION
    )
    + tuple(
        (
            f"skill:survival-{name}",
            f"Survival ({name.replace('-', ' ').title()})",
            hazard(f"survive-{name}-environment", "survival-environment"),
        )
        for name in _SURVIVAL
    )
)

PROCEDURES: Final = MappingProxyType(
    {identifier: bind(identifier, name, OWNER, selected) for identifier, name, selected in _ROWS}
)


def definitions() -> tuple[RuleDefinition, ...]:
    """Return concrete definitions; open families and techniques do not manufacture skills."""
    return tuple(
        procedure.definition()
        for procedure in PROCEDURES.values()
        if procedure.dispatchable and procedure.spec() is not None
    )
