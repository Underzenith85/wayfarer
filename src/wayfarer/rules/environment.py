"""Explicit Basic Set environmental variants (B93, B430, B434, B439, B443)."""

from wayfarer.errors import ValidationError
from wayfarer.rules.hazard_types import HazardSpec


def ambient_spec(
    spec: HazardSpec,
    *,
    temperature: int,
    ht: int,
    tolerance: int = 0,
    cold_extension: int = 0,
    center: int = 62,
    wind: int = 0,
    clothing: str = "winter",
    wet: bool = False,
) -> HazardSpec:
    # Default human range is 35..90; a racial center shifts both endpoints.
    if tolerance < 0 or not 0 <= cold_extension <= ht * tolerance or wind < 0:
        raise ValidationError("Invalid temperature tolerance allocation")
    low = 35 + center - 62 - cold_extension
    high = 90 + center - 62 + ht * tolerance - cold_extension
    if spec.kind == "cold":
        if temperature >= low:
            raise ValidationError("Temperature is within the actor's cold comfort limit")
        clothing_bonus = {"light": -5, "winter": 0, "arctic": 5, "heated": 10}
        if clothing not in clothing_bonus:
            raise ValidationError("Unknown cold protection")
        modifier = clothing_bonus[clothing] - 5 * wet - max(0, (low - 35 - temperature) // 10)
        interval = 600 if wind >= 30 else 900 if wind >= 10 else 1800
    elif spec.kind == "heat":
        if temperature <= high - 10:
            raise ValidationError("Temperature is below the actor's heat-check threshold")
        modifier = -max(0, (temperature - high) // 10)
        interval = 1800
    else:
        raise ValidationError("Ambient temperature requires cold or heat")
    return HazardSpec.model_validate(
        spec.model_copy(
            update={"resistance_modifier": modifier, "delay": interval, "interval": interval}
        )
    )


def poison_spec(
    variant: str,
    *,
    id: str,
    scene_id: str,
    exposure_seconds: int = 0,
    affliction_seconds: int | None = None,
) -> HazardSpec:
    """B439 named constructions; callers still bind actual delivery/protection."""
    rows = {
        "arsenic": (3600, 3600, 8, -2, 1, 0, True),
        "cobra-venom": (60, 3600, 6, -3, 2, 0, True),
        "cyanide-blood": (0, 1, 1, 0, 4, 0, False),
        "cyanide-digestive": (900, 1, 1, 0, 4, 0, False),
        "mustard-contact": (0, 28800, 24, -4, 0, 1, True),
        "mustard-respiratory": (7200, 3600, 6, -1, 1, 0, True),
        "nerve-gas-paralysis": (0, 60, 6, -6, 2, 0, True),
        "smoke": (10, 1, 1, 0, 0, 0, True),
        "tear-gas-respiratory": (0, 1, 1, -2, 0, 0, True),
        "tear-gas-vision": (0, 1, 1, -2, 0, 0, True),
    }
    if exposure_seconds < 0 or (affliction_seconds is not None and affliction_seconds < 1):
        raise ValidationError("Invalid poison exposure duration")
    if variant == "nerve-gas-paralysis" and affliction_seconds is None:
        raise ValidationError("B439 requires an authored duration for the nerve agent's affliction")
    if variant not in rows:
        raise ValidationError("Unknown Basic Set poison variant")
    delay, interval, cycles, modifier, dice, adds, resistible = rows[variant]
    return HazardSpec(
        id=id,
        scene_id=scene_id,
        kind="poison",
        variant=variant,
        delay=delay,
        interval=interval,
        cycles=cycles,
        resistance_modifier=modifier,
        damage_dice=dice,
        damage_add=adds,
        resistible=resistible,
        reference="B439",
        affliction="paralysis"
        if variant == "nerve-gas-paralysis"
        else "blindness"
        if variant == "tear-gas-vision"
        else "coughing"
        if variant in ("smoke", "tear-gas-respiratory")
        else "none",
        affliction_seconds=affliction_seconds or exposure_seconds,
        duration_per_margin=60 if variant.startswith("tear-gas") or variant == "smoke" else 0,
    )
