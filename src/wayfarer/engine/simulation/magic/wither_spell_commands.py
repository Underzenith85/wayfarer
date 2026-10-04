"""Private source-fixed Wither Limb command carrier (B244)."""

from typing import Literal

from pydantic import TypeAdapter

from wayfarer.engine.simulation.magic.melee_spell_state import StaffCarrier
from wayfarer.engine.simulation.resources import Command
from wayfarer.models import Id


class CastWitherLimb(Command):
    kind: Literal["cast-wither-limb"] = "cast-wither-limb"
    operation: Literal["start", "concentrate", "complete", "cancel"]
    cast_id: Id
    carrier: StaffCarrier

    @property
    def energy(self) -> int:
        return 5


ADAPTER: TypeAdapter[CastWitherLimb] = TypeAdapter(CastWitherLimb)
