"""Technology-level parameters for first aid and physician care (B424)."""

from __future__ import annotations

from wayfarer.errors import ValidationError


def first_aid_parameters(tl: int) -> tuple[int, int]:
    """Seconds and modifier to 1d; high-HP multiplier is applied separately."""
    if not 0 <= tl <= 12:
        raise ValidationError("Unsupported medical technology level")
    return (
        1800 if tl < 5 else 1200 if tl < 8 else 600,
        -4 if tl < 2 else -3 if tl < 4 else -2 if tl < 6 else -1 if tl < 8 else 0 if tl == 8 else 1,
    )


def physician_parameters(tl: int) -> tuple[int, int]:
    if not 1 <= tl <= 12:
        raise ValidationError("Physician treatment requires TL1 or later")
    days, patients = {
        1: (7, 10),
        2: (7, 10),
        3: (7, 10),
        4: (3, 10),
        5: (2, 15),
        6: (1, 20),
        7: (1, 25),
        8: (1, 50),
        9: (1, 50),
        10: (1, 50),
        11: (1, 100),
        12: (1, 200),
    }[tl]
    return days * 86400 // (tl - 7 if tl >= 9 else 1), patients
