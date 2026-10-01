"""Combat projections from approved canonical mastery purchases, B93/B99."""

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.traits.mastery import covers


def trained_by_master(build: ValidatedBuild) -> bool:
    return any(p.definition_id == "trait:advantage:trained-by-a-master" for p in build.purchases)


def weapon_master(build: ValidatedBuild, weapon_id: str, skill_id: str, hands: int) -> bool:
    if not any(p.definition_id == skill_id for p in build.purchases):
        return False  # B99 explicitly excludes default use.
    return any(
        p.definition_id == "trait:advantage:weapon-master"
        and p.trait is not None
        and covers(p.trait, weapon_id, hands, skill_id)
        for p in build.trait_purchases
    )


def damage_bonus(
    build: ValidatedBuild,
    weapon_id: str,
    skill_id: str,
    hands: int,
    dice: int,
    *,
    applies: bool = True,
) -> int:
    if (
        not applies
        or build.statistics is None
        or not weapon_master(build, weapon_id, skill_id, hands)
    ):
        return 0
    value = next((int(v.value) for v in build.sheet.values if v.target == skill_id), 0)
    relative = value - build.statistics.dx
    return dice * (2 if relative >= 2 else 1 if relative >= 1 else 0)


def parry_multiplier(build: ValidatedBuild, weapon_id: str, skill_id: str, hands: int) -> int:
    return 2 if trained_by_master(build) or weapon_master(build, weapon_id, skill_id, hands) else 1
