"""Basic Set unarmed skill permissions and critical-miss rows (B370, B403, B557).

The table describes outcomes; simulation decides whether a supported state
transition can execute one. Contextual outcomes retain their existing pause.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from wayfarer.errors import ValidationError

UNARMED_SKILLS = MappingProxyType(
    {
        "punch": frozenset({"attribute:dx", "skill:brawling", "skill:boxing", "skill:karate"}),
        "kick": frozenset({"attribute:dx", "skill:brawling", "skill:karate"}),
        "grapple": frozenset(
            {"attribute:dx", "skill:judo", "skill:wrestling", "skill:sumo-wrestling"}
        ),
        "arm_lock": frozenset({"skill:judo", "skill:wrestling"}),
    }
)

MissEffect = Literal[
    "unconscious", "strain", "self-hit", "stumble", "fall", "balance", "trip", "guard", "tear"
]


@dataclass(frozen=True)
class UnarmedMissRule:
    effect: MissEffect
    half_damage: bool = False
    defense_penalty: int = 0
    strain_seconds: int = 0
    trip_kick_penalty: int = 0


_UNARMED_MISSES = (
    UnarmedMissRule("unconscious"),  # 3
    UnarmedMissRule("strain", strain_seconds=1800),
    UnarmedMissRule("self-hit"),
    UnarmedMissRule("self-hit", half_damage=True),
    UnarmedMissRule("stumble"),
    UnarmedMissRule("fall"),
    UnarmedMissRule("balance", defense_penalty=-2),
    UnarmedMissRule("balance", defense_penalty=-2),
    UnarmedMissRule("balance", defense_penalty=-2),
    UnarmedMissRule("trip", trip_kick_penalty=-4),
    UnarmedMissRule("guard", defense_penalty=-2),
    UnarmedMissRule("stumble"),
    UnarmedMissRule("tear", defense_penalty=-1),
    UnarmedMissRule("self-hit"),
    UnarmedMissRule("strain", strain_seconds=1800),
    UnarmedMissRule("unconscious"),  # 18
)


def unarmed_critical_miss(total: int) -> UnarmedMissRule:
    if not 3 <= total <= 18:
        raise ValidationError("Unarmed critical miss requires a 3d total")
    return _UNARMED_MISSES[total - 3]
