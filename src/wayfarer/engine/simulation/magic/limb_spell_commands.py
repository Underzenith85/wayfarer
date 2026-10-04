"""Private source-fixed Paralyze Limb command carrier (B244)."""

from typing import Literal

from pydantic import TypeAdapter

from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
from wayfarer.engine.simulation.resources import Command
from wayfarer.models import Id


class CastParalyzeLimb(Command):
    kind: Literal["cast-paralyze-limb"] = "cast-paralyze-limb"
    operation: Literal["start", "concentrate", "complete", "cancel"]
    cast_id: Id
    carrier: StaffCarrier

    @property
    def energy(self) -> int:
        return 3


ADAPTER: TypeAdapter[CastParalyzeLimb] = TypeAdapter(CastParalyzeLimb)
