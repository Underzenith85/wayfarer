"""Pinned environmental hazard authoring profiles (Campaigns B428-B437).

These constructors turn trusted scenario measurements into persisted facts.  They
do not detect an atmosphere, infer a seal, or accept player-authored damage.
"""

from __future__ import annotations

from typing import Literal

from wayfarer.engine.rules.types.hazard import (
    HazardEnvironment,
    HazardProtection,
    HazardSchedule,
    HazardSpec,
)
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class CombustionFacts(Record):
    """Authored material class and beam geometry for B433 ignition."""

    material: Literal[
        "super-flammable",
        "highly-flammable",
        "flammable",
        "resistant",
        "highly-resistant",
        "nonflammable",
    ]
    tight_beam: bool = False


def _environment(
    medium: Literal[
        "contact",
        "air",
        "water",
        "fire",
        "electric-current",
        "acceleration",
        "radiation",
        "vacuum",
        "vessel-motion",
    ],
    *,
    intensity: int,
    duration: int,
    source: str,
    pressure: int | None = None,
) -> HazardEnvironment:
    return HazardEnvironment(
        medium=medium,
        intensity=intensity,
        duration_seconds=duration,
        pressure_milli_atmospheres=pressure,
        source_class=source,
    )


def acid_spec(
    variant: Literal["splash", "immersion", "swallowed"],
    *,
    id: str,
    scene_id: str,
    protection: HazardProtection,
) -> HazardSpec:
    """B428: strong-acid contact and ingested-delay profiles."""
    rows = {
        "splash": (0, 1, 1, 1, -3, "contact"),
        "immersion": (0, 1, 100000, 1, -1, "contact"),
        "swallowed": (900, 900, 18, 0, 1, "contact"),
    }
    delay, interval, cycles, dice, add, _ = rows[variant]
    return HazardSpec(
        id=id,
        kind="acid",
        scene_id=scene_id,
        variant=variant,
        delay=delay,
        interval=interval,
        cycles=cycles,
        cycles_dice=3 if variant == "swallowed" else 0,
        damage_dice=dice,
        damage_add=add,
        resistible=False,
        damage_type="cor",
        environment=_environment(
            "contact", intensity=1, duration=interval * cycles, source="strong-acid"
        ),
        protection=protection,
        reference="B428",
    )


def atmosphere_spec(
    variant: Literal[
        "corrosive-trace", "toxic-pollutant", "toxic-lethal", "toxic-dense", "suffocating"
    ],
    *,
    id: str,
    scene_id: str,
    severity: int,
    duration: int,
    protection: HazardProtection,
) -> HazardSpec:
    """B429: explicit composition, concentration, duration and protection."""
    kind: Literal["atmosphere", "vacuum"]
    damage_type: Literal["cor", "tox"] | None
    if variant == "corrosive-trace":
        if severity not in range(0, 5):
            raise ValidationError("Corrosive atmosphere severity must be HT to HT-4")
        kind, interval, dice, add, resistible, damage_type = "atmosphere", 60, 0, 1, True, "cor"
    elif variant == "toxic-pollutant":
        kind, interval, dice, add, resistible, damage_type = (
            "atmosphere",
            86400,
            0,
            1,
            True,
            "tox",
        )
        severity = 0
    elif variant == "toxic-lethal":
        if severity not in range(2, 7):
            raise ValidationError("Lethal gas severity must be HT-2 through HT-6")
        kind, interval, dice, add, resistible, damage_type = "atmosphere", 60, 0, 1, True, "tox"
    elif variant == "toxic-dense":
        kind, interval, dice, add, resistible, damage_type = (
            "atmosphere",
            15,
            max(1, severity),
            0,
            False,
            "tox",
        )
        severity = 0
    else:
        kind, interval, dice, add, resistible, damage_type = "vacuum", 1, 0, 1, False, None
        severity = 0
        duration = max(duration, 240)
    cycles = max(1, (duration + interval - 1) // interval)
    return HazardSpec(
        id=id,
        kind=kind,
        scene_id=scene_id,
        variant=variant,
        delay=interval,
        interval=interval,
        cycles=cycles,
        resistance_modifier=-severity,
        damage_dice=dice,
        damage_add=add,
        resistible=resistible,
        damage_type=damage_type,
        environment=_environment(
            "air", intensity=max(1, severity), duration=duration, source=variant
        ),
        protection=protection,
        reference="B429",
    )


def pressure_spec(
    variant: Literal["crushing", "bends"],
    *,
    id: str,
    scene_id: str,
    pressure_milli_atmospheres: int,
    duration: int,
    protection: HazardProtection,
) -> HazardSpec:
    """B429/B435: native-pressure ratios remain authored integer measurements."""
    if pressure_milli_atmospheres <= 0:
        raise ValidationError("Pressure exposure requires a positive measured pressure")
    ratio = max(1, pressure_milli_atmospheres // 1000)
    if variant == "crushing":
        divisor = 100 if protection.pressure_support == 2 else 10
        modifier = 3 - ratio // divisor
        return HazardSpec(
            id=id,
            kind="pressure",
            scene_id=scene_id,
            variant=variant,
            interval=60,
            cycles=max(1, (duration + 59) // 60),
            resistance_modifier=modifier,
            damage_add=0,
            damage_from_margin=True,
            damage_type="cr",
            environment=_environment(
                "air",
                intensity=ratio,
                duration=duration,
                source="superdense-pressure",
                pressure=pressure_milli_atmospheres,
            ),
            protection=protection,
            reference="B429/B435",
        )
    return HazardSpec(
        id=id,
        kind="pressure",
        scene_id=scene_id,
        variant=variant,
        interval=3600,
        cycles=max(1, (duration + 3599) // 3600),
        damage_dice=1,
        damage_add=0,
        damage_type="cr",
        affliction="paralysis",
        critical_effect="death",
        environment=_environment(
            "water",
            intensity=ratio,
            duration=duration,
            source="rapid-decompression",
            pressure=pressure_milli_atmospheres,
        ),
        protection=protection,
        reference="B435",
    )


def thermal_shock_spec(
    *, id: str, scene_id: str, duration: int, dry_suit: bool
) -> HazardSpec:
    """B430: sudden icy immersion ignores ordinary clothing; a dry suit diverts to cold."""
    protection = HazardProtection(sealed=dry_suit, insulated=dry_suit)
    return HazardSpec(
        id=id,
        kind="cold",
        scene_id=scene_id,
        variant="thermal-shock",
        interval=60,
        cycles=max(1, (duration + 59) // 60),
        damage_add=1,
        damage_from_margin=True,
        environment=_environment(
            "water", intensity=1, duration=duration, source="icy-immersion"
        ),
        protection=protection,
        reference="B430",
    )


def intense_heat_spec(
    *, id: str, scene_id: str, dr: int, duration: int, protection: HazardProtection
) -> HazardSpec:
    """B434: ambient overheating starts after three seconds per point of DR."""
    if dr < 0:
        raise ValidationError("Heat protection DR cannot be negative")
    return HazardSpec(
        id=id,
        kind="heat",
        scene_id=scene_id,
        variant="intense-heat",
        delay=max(1, 3 * dr),
        interval=1,
        cycles=max(1, duration),
        damage_add=1,
        environment=_environment(
            "fire", intensity=1, duration=duration, source="intense-ambient-heat"
        ),
        protection=protection,
        reference="B434",
    )


def decay_radiation(schedule: HazardSchedule, *, at: int) -> HazardSchedule:
    """B435: after 30 days, heal 10 rads/day down to 10% of the original dose."""
    received = schedule.radiation_received_at
    starts = received + 30 * 86400 if received is not None else None
    if starts is None or at <= starts:
        return schedule
    elapsed_days = (at - max(starts, schedule.radiation_decayed_at or starts)) // 86400
    floor = (schedule.radiation_original + 9) // 10
    return schedule.model_copy(
        update={
            "radiation_dose": max(floor, schedule.radiation_dose - elapsed_days * 10),
            "radiation_decayed_at": at,
        }
    )


def electricity_spec(
    variant: Literal["nonlethal", "lethal", "localized"],
    *,
    id: str,
    scene_id: str,
    strength_modifier: int,
    damage_dice: int = 0,
    continuous_seconds: int = 1,
    protection: HazardProtection,
) -> HazardSpec:
    """B432-433: current strength and insulation are independent authored facts."""
    if not -5 <= strength_modifier <= 2 or continuous_seconds < 1:
        raise ValidationError("Unsupported electrical strength or duration")
    lethal = variant != "nonlethal"
    if lethal != (damage_dice > 0):
        raise ValidationError("Only lethal electrical variants author burning damage")
    return HazardSpec(
        id=id,
        kind="electricity",
        scene_id=scene_id,
        variant=variant,
        interval=1,
        cycles=continuous_seconds,
        resistance_modifier=strength_modifier + protection.nonmetallic_dr,
        damage_dice=damage_dice,
        damage_add=0,
        resistible=not lethal,
        damage_type="burn" if lethal else None,
        critical_effect="heart-attack" if variant == "lethal" else "none",
        environment=_environment(
            "electric-current",
            intensity=max(1, damage_dice or 1),
            duration=continuous_seconds,
            source=variant,
        ),
        protection=protection,
        reference="B432-433",
    )


def acceleration_spec(
    *,
    id: str,
    scene_id: str,
    centigravity: int,
    home_centigravity: int,
    duration: int,
    posture: Literal["upright", "seated", "prone", "inverted"],
    protection: HazardProtection,
) -> HazardSpec:
    """B434: sudden acceleration uses an authored ratio and body posture."""
    home = max(10, home_centigravity)
    ratio = centigravity / home
    if ratio < 2.5:
        raise ValidationError("Acceleration below 2.5 home gravities has no health procedure")
    doubling = 0
    threshold = 5.0
    while ratio >= threshold:
        doubling += 1
        threshold *= 2
    posture_modifier = 2 if posture in ("seated", "prone") else -2 if posture == "inverted" else 0
    return HazardSpec(
        id=id,
        kind="acceleration",
        scene_id=scene_id,
        variant=posture,
        interval=max(1, duration),
        cycles=1,
        resistance_modifier=posture_modifier - 2 * doubling,
        damage_add=0,
        damage_from_margin=True,
        critical_effect="unconscious",
        environment=_environment(
            "acceleration", intensity=centigravity, duration=duration, source="sudden-g"
        ),
        protection=protection,
        reference="B434",
    )


def radiation_spec(
    *,
    id: str,
    scene_id: str,
    rads: int,
    interval: int,
    cycles: int,
    protection: HazardProtection,
) -> HazardSpec:
    """B435-436: a dated dose retains its source and explicit protection factor."""
    return HazardSpec(
        id=id,
        kind="radiation",
        scene_id=scene_id,
        variant="dose",
        interval=interval,
        cycles=cycles,
        damage_add=0,
        radiation_rads=rads,
        environment=_environment(
            "radiation", intensity=rads, duration=interval * cycles, source="ionizing-radiation"
        ),
        protection=protection,
        reference="B435-436",
    )


def seasickness_spec(
    *, id: str, scene_id: str, duration: int, protection: HazardProtection
) -> HazardSpec:
    """B436: one first-day shipboard check for characters without Motion Sickness."""
    return HazardSpec(
        id=id,
        kind="seasickness",
        scene_id=scene_id,
        interval=duration,
        cycles=1,
        resistance_modifier=5,
        damage_add=0,
        affliction="retching",
        affliction_seconds=1,
        environment=_environment(
            "vessel-motion", intensity=1, duration=duration, source="unstabilized-vessel"
        ),
        protection=protection,
        reference="B436",
    )


def vacuum_spec(
    *,
    id: str,
    scene_id: str,
    blood_oxygen_seconds: int,
    explosive: bool,
    protection: HazardProtection,
) -> HazardSpec:
    """B437: authored protection and blood-oxygen time precede suffocation ticks."""
    return HazardSpec(
        id=id,
        kind="vacuum",
        scene_id=scene_id,
        variant="explosive" if explosive else "exhaled",
        delay=max(1, blood_oxygen_seconds),
        interval=1,
        cycles=240,
        damage_dice=1 if explosive else 0,
        damage_add=0 if explosive else 1,
        resistible=False,
        damage_type="cr" if explosive else None,
        environment=_environment(
            "vacuum", intensity=1, duration=240, source="vacuum", pressure=0
        ),
        protection=protection,
        reference="B437",
    )


_IGNITION = {
    "super-flammable": 0,
    "highly-flammable": 1,
    "flammable": 3,
    "resistant": 10,
    "highly-resistant": 30,
}


def ignition_threshold(
    material: Literal[
        "super-flammable", "highly-flammable", "flammable", "resistant", "highly-resistant", "nonflammable"
    ],
    *,
    tight_beam: bool = False,
) -> int | None:
    """B433: damage required in one roll; callers keep object damage authoritative."""
    value = _IGNITION.get(material)
    return None if value is None else value * (10 if tight_beam else 1)
