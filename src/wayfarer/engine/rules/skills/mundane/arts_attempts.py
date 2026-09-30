"""Execute and replay arts, crafts and trade procedures (#338)."""

from __future__ import annotations

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.skills.mundane.arts import OBJECT_REPAIR_SKILLS, PROCEDURES
from wayfarer.engine.rules.skills.mundane.procedures import (
    PROFILE,
    Performer,
    ProcedureResult,
    Situation,
    replay,
)
from wayfarer.engine.rules.skills.mundane.procedures import (
    attempt as attempt_procedure,
)
from wayfarer.errors import ValidationError

__all__ = ["Performer", "ProcedureResult", "Situation", "attempt", "replay"]


def attempt(
    performer: Performer,
    situation: Situation,
    *,
    rng: RandomSource,
    profile_id: str = PROFILE,
) -> ProcedureResult:
    """Execute the exact procedure named by ``performer.skill_id``."""
    if performer.skill_id in OBJECT_REPAIR_SKILLS:
        raise ValidationError(
            "Armoury restoration requires the owned-equipment repair transaction"
        )
    return attempt_procedure(PROCEDURES, performer, situation, rng=rng, profile_id=profile_id)
