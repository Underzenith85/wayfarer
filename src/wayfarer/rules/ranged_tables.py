"""Pure ranged-combat lookups from the Basic Set: range penalties and rapid-fire bonuses."""


def range_penalty(yards: float) -> int:
    """B550 size/speed/range progression, rounded up to the next entry."""
    if yards <= 2:
        return 0
    bounds = (3, 5, 7, 10, 15, 20)
    decade = 1
    penalty = 1
    while True:
        for bound in bounds:
            if yards <= bound * decade:
                return -penalty
            penalty += 1
        decade *= 10


def rapid_fire_bonus(shots: int) -> int:
    """B373 rapid-fire bonus, bounded to the currently supported effective RoF <= 100."""
    return next(
        bonus
        for limit, bonus in ((4, 0), (8, 1), (12, 2), (16, 3), (24, 4), (49, 5), (99, 6), (100, 7))
        if shots <= limit
    )


def multiple_projectile_attack(
    shots: int,
    projectiles_per_shot: int,
    distance_yards: float,
    half_damage_range: float,
) -> tuple[int, int]:
    """Return B409 effective RoF and close-range damage/DR multiplier.

    ``shots`` always remains the number of shells consumed. At less than 10%
    of 1/2D the projectiles have not dispersed, so the shell is one hit whose
    damage expression and target DR share the same multiplier.
    """
    if distance_yards < half_damage_range / 10:
        return shots, projectiles_per_shot // 2
    return shots * projectiles_per_shot, 1
