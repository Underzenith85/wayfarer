"""Domain-only catalog binding for the representative ability runtime."""

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.engine.rules.traits.modifiers import ModifierSelection
from wayfarer.models import Record


class AbilitySpec(Record):
    definition_id: str = Field(min_length=1, max_length=200)
    kind: Literal["burning-malediction", "damage-resistance", "detect", "mind-reading"]
    modifiers: tuple[str, ...] = ()
    gadget_modifiers: tuple[ModifierSelection, ...] = ()
    gadget_actor_id: str | None = Field(default=None, min_length=1, max_length=200)
    gadget_item_id: str | None = Field(default=None, min_length=1, max_length=200)
    gadget_definition_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def exact_gadget_binding(self) -> Self:
        binding = (self.gadget_actor_id, self.gadget_item_id, self.gadget_definition_id)
        if any(value is not None for value in binding) != all(
            value is not None for value in binding
        ):
            raise ValueError("Gadget ability binding requires actor, item, and definition")
        identifiers = tuple(row.definition_id for row in self.gadget_modifiers)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate gadget modifier binding")
        selected = {value for value in self.modifiers if value.startswith("modifier:gadget-")}
        if selected != set(identifiers):
            raise ValueError("Purchased gadget modifiers require exact authored facts")
        if bool(identifiers) != all(value is not None for value in binding):
            raise ValueError("Gadget modifiers require one exact authoritative item binding")
        return self
