"""Opt-in entangling weapon facts and the durable binding they create (#354).

Bolas (B181) and Net (B211) do not resolve as ordinary projectiles: a hit binds
the target until it breaks free. The numbers are pinned catalog metadata, never
inferred from a skill name, damage type or weapon weight, so an authored catalog
supplies them and this module only carries the shape and the invariants.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Record


class EntangleSpec(Record):
    """Explicit binding facts for one entangling weapon mode."""

    # The binding's own ST, contested when the victim tries to break free.
    binding_st: int = Field(ge=1, le=40)
    # Penalties the binding imposes while it holds. Both are nonpositive: an
    # entangling weapon never improves the victim's rolls.
    attack_penalty: int = Field(le=0, ge=-20)
    defense_penalty: int = Field(le=0, ge=-20)
    # A binding that pins the legs stops movement; one that binds an arm does not.
    immobilizes: bool
    # Whether the weapon stays on the victim after a hit. A weapon that does not
    # stay attached is expended by the throw like any other thrown projectile.
    attached: bool
    # Optional trained alternative to the raw ST contest. The skill is resolved
    # through the pinned package like any other; naming one here never creates it.
    escape_skill_id: str | None = Field(default=None, min_length=1)


class Entanglement(Record):
    """One live binding on a combatant, carried in the encounter checkpoint."""

    source_actor_id: str = Field(min_length=1)
    weapon_definition_id: str = Field(min_length=1)
    mode_id: str = Field(min_length=1)
    binding_st: int = Field(ge=1, le=40)
    attack_penalty: int = Field(le=0, ge=-20)
    defense_penalty: int = Field(le=0, ge=-20)
    immobilizes: bool
    attached: bool
    escape_skill_id: str | None = Field(default=None, min_length=1)
    # Failed attempts are retained so a replayed encounter reproduces the
    # sequence rather than restarting the victim's struggle.
    attempts: int = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def bound_by_another(self) -> Self:
        if self.source_actor_id == self.weapon_definition_id:
            raise ValueError("A binding names its thrower and its weapon separately")
        return self

    @classmethod
    def bind(
        cls, spec: EntangleSpec, *, source_actor_id: str, weapon_definition_id: str, mode_id: str
    ) -> Self:
        return cls(
            source_actor_id=source_actor_id,
            weapon_definition_id=weapon_definition_id,
            mode_id=mode_id,
            binding_st=spec.binding_st,
            attack_penalty=spec.attack_penalty,
            defense_penalty=spec.defense_penalty,
            immobilizes=spec.immobilizes,
            attached=spec.attached,
            escape_skill_id=spec.escape_skill_id,
        )


EscapeOutcome = Literal["freed", "held"]
