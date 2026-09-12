"""Combat technique procedures from Basic Set B230-232 (#340)."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy as P
from wayfarer.engine.rules.skills.mundane.procedures import Resolution as R
from wayfarer.engine.rules.skills.mundane.procedures import Task, bind

OWNER: Final = 340
MELEE: Final = "combat.melee-attack"
UNARMED: Final = "combat.unarmed"
DEFENSE: Final = "combat.active-defense"
RANGED: Final = "combat.ranged-attack"
TACTICAL: Final = "combat.tactical"


def task(
    dispatch: str,
    effect: str,
    unit: str = "combat-resolution",
    *,
    resolution: R = R.SUCCESS_ROLL,
    policy: P = P.RETRY_UNTIL_SUCCESS,
    context: tuple[str, ...] = (),
    modifiers: frozenset[str] = frozenset(),
    margin: bool = False,
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
    )


STRIKE = frozenset({"maneuver", "posture", "visibility", "hit-location"})
CONTROL = frozenset({"relative-strength", "posture", "grip", "injury"})
WEAPON = frozenset({"weapon-reach", "weapon-quality", "off-hand", "posture"})
MOUNTED = frozenset({"mount-control", "range", "speed", "posture"})


def strike(effect: str, *context: str) -> Task:
    return task(
        UNARMED,
        effect,
        context=("target-in-reach",) + context,
        modifiers=STRIKE,
    )


def control(effect: str, *, contest: bool = False) -> Task:
    return task(
        UNARMED,
        effect,
        "control-margin" if contest else "combat-resolution",
        resolution=R.QUICK_CONTEST if contest else R.SUCCESS_ROLL,
        context=("established-grip",),
        modifiers=CONTROL,
        margin=contest,
    )


_ROWS: Final = (
    ("skill:arm-lock", "Arm Lock", control("apply-arm-lock")),
    ("skill:arm-lock-judo", "Arm Lock (Judo)", control("apply-judo-arm-lock")),
    ("skill:back-kick", "Back Kick", strike("back-kick", "target-behind")),
    ("skill:choke-hold", "Choke Hold", control("apply-choke-hold", contest=True)),
    (
        "skill:disarming",
        "Disarming",
        task(
            MELEE,
            "disarm-opponent",
            "disarm-margin",
            resolution=R.QUICK_CONTEST,
            context=("opponent-weapon", "target-in-reach"),
            modifiers=WEAPON,
            margin=True,
        ),
    ),
    (
        "skill:dual-weapon-attack",
        "Dual-Weapon Attack",
        task(
            MELEE,
            "attack-with-two-weapons",
            context=("two-ready-weapons", "declared-targets"),
            modifiers=WEAPON,
        ),
    ),
    ("skill:elbow-strike", "Elbow Strike", strike("elbow-strike")),
    (
        "skill:feint",
        "Feint",
        task(
            TACTICAL,
            "impose-defense-penalty",
            "defense-penalty",
            resolution=R.QUICK_CONTEST,
            context=("opponent-present",),
            modifiers=WEAPON,
            margin=True,
        ),
    ),
    ("skill:finger-lock", "Finger Lock", control("apply-finger-lock")),
    (
        "skill:ground-fighting",
        "Ground Fighting",
        task(
            DEFENSE,
            "offset-ground-combat-penalty",
            context=("grounded",),
            modifiers=STRIKE | CONTROL,
        ),
    ),
    (
        "skill:horse-archery",
        "Horse Archery",
        task(
            RANGED,
            "shoot-from-mount",
            "attack-resolution",
            context=("controlled-mount", "ready-bow", "target-in-range"),
            modifiers=MOUNTED,
        ),
    ),
    ("skill:jump-kick", "Jump Kick", strike("jump-kick", "running-start")),
    ("skill:kicking", "Kicking", strike("kick")),
    ("skill:kicking-karate", "Kicking (Karate)", strike("karate-kick")),
    ("skill:knee-strike", "Knee Strike", strike("knee-strike", "close-combat")),
    (
        "skill:neck-snap",
        "Neck Snap",
        task(
            UNARMED,
            "snap-neck",
            "control-resolution",
            resolution=R.REGULAR_CONTEST,
            context=("neck-grip",),
            modifiers=CONTROL,
        ),
    ),
    (
        "skill:off-hand-weapon-training",
        "Off-Hand Weapon Training",
        task(
            MELEE,
            "waive-off-hand-penalty",
            context=("off-hand-weapon",),
            modifiers=WEAPON,
        ),
    ),
    (
        "skill:retain-weapon",
        "Retain Weapon",
        task(
            DEFENSE,
            "retain-weapon",
            "retention-margin",
            resolution=R.QUICK_CONTEST,
            context=("opponent-disarm-attempt",),
            modifiers=WEAPON,
            margin=True,
        ),
    ),
    ("skill:sweep", "Sweep", strike("sweep-target", "standing-target")),
    (
        "skill:whirlwind-attack",
        "Whirlwind Attack",
        task(
            MELEE,
            "attack-every-adjacent-foe",
            "attack-resolution",
            context=("ready-weapon", "adjacent-targets"),
            modifiers=WEAPON,
        ),
    ),
)

PROCEDURES: Final = MappingProxyType(
    {identifier: bind(identifier, name, OWNER, selected) for identifier, name, selected in _ROWS}
)


def definitions() -> tuple[RuleDefinition, ...]:
    """Return the two source-concrete techniques; templates expand per parent."""
    return tuple(
        procedure.definition()
        for procedure in PROCEDURES.values()
        if procedure.dispatchable and procedure.spec() is not None
    )
