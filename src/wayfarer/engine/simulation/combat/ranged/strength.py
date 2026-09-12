"""Whether a wielder can handle a weapon rated for a minimum strength."""

from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.errors import ValidationError


def validate_rated_strength(profile_id: str, weapon: RangedMode, st: int) -> None:
    if weapon.rated_strength is None:
        return
    if profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Rated weapon ST requires the exact Basic Set profile")
    if weapon.rated_strength.kind == "bow" and weapon.rated_strength.st > st:
        raise ValidationError("Bow ST exceeds the wielder's effective ST")
