"""B240 physical Staff forms bound to the actual selected melee mode."""

from wayfarer.engine.rules.magic.protocols import MagicStaff
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.errors import ValidationError


def require_staff_mode(staff: MagicStaff, selected: MeleeMode | RangedMode) -> None:
    """Ordinary damaging contact supports baton and quarterstaff forms only."""
    if staff.form == "wand":
        raise ValidationError("A Staff wand requires a contact attack without a physical blow")
    if (
        not isinstance(selected, MeleeMode)
        or selected.skill_id not in tuple("skill:" + skill for skill in staff.weapon_skills)
        or max(selected.reach) > int(staff.reach)
        or selected.damage.damage_type != "cr"
        or selected.damage.basis not in ("thrust", "swing")
    ):
        raise ValidationError("Held Staff contact requires its B240 physical form's melee mode")
