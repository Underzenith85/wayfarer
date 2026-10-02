"""Private Cyclic payloads; portable scenario contracts retain their frozen vocabulary."""

from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.cyclic import (
    CyclicAttack,
    CyclicExposure,
    ZeroDamageCyclicAttack,
    ZeroDamageCyclicExposure,
)
from wayfarer.engine.rules.types.symptoms import SymptomSpec


class MultipleSymptomsCyclicAttack(CyclicAttack):
    """An approved runtime source carrying more than one B109 effect."""

    symptom_spec: SymptomSpec
    additional_symptoms: tuple[SymptomSpec, ...] = Field(min_length=1)


class ZeroDamageMultipleSymptomsCyclicAttack(MultipleSymptomsCyclicAttack):
    """B378 zero initial damage retains every effect for later Cyclic injury."""

    basic_damage: Literal[0] = Field()


class MultipleSymptomsCyclicExposure(CyclicExposure):
    """A contact snapshot retaining the complete private source payload."""

    source: MultipleSymptomsCyclicAttack


class ZeroDamageMultipleSymptomsCyclicExposure(CyclicExposure):
    """A contact snapshot of a zero-initial-damage source with multiple effects."""

    source: ZeroDamageMultipleSymptomsCyclicAttack


type RuntimeCyclicAttack = (
    CyclicAttack
    | ZeroDamageCyclicAttack
    | MultipleSymptomsCyclicAttack
    | ZeroDamageMultipleSymptomsCyclicAttack
)
type RuntimeCyclicExposure = (
    CyclicExposure
    | ZeroDamageCyclicExposure
    | MultipleSymptomsCyclicExposure
    | ZeroDamageMultipleSymptomsCyclicExposure
)


def additional_symptoms(attack: CyclicAttack) -> tuple[SymptomSpec, ...]:
    return attack.additional_symptoms if isinstance(attack, MultipleSymptomsCyclicAttack) else ()


def create_attack(data: dict[str, object]) -> RuntimeCyclicAttack:
    """Validate the matching runtime shape without changing single-effect records."""
    if data.get("additional_symptoms"):
        kind = (
            ZeroDamageMultipleSymptomsCyclicAttack
            if data.get("basic_damage") == 0
            else MultipleSymptomsCyclicAttack
        )
        return kind.model_validate(data)
    ordinary = {key: value for key, value in data.items() if key != "additional_symptoms"}
    base = ZeroDamageCyclicAttack if data.get("basic_damage") == 0 else CyclicAttack
    return base.model_validate(ordinary)


def create_exposure(source: CyclicAttack, data: dict[str, object]) -> RuntimeCyclicExposure:
    """Preserve the validated concrete source when recording a contagious contact."""
    payload = {**data, "source": source}
    if isinstance(source, ZeroDamageMultipleSymptomsCyclicAttack):
        return ZeroDamageMultipleSymptomsCyclicExposure.model_validate(payload)
    if isinstance(source, MultipleSymptomsCyclicAttack):
        return MultipleSymptomsCyclicExposure.model_validate(payload)
    if isinstance(source, ZeroDamageCyclicAttack):
        return ZeroDamageCyclicExposure.model_validate(payload)
    return CyclicExposure.model_validate(payload)
