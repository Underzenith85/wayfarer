"""B273/B432 cattle-prod linked electrical affliction."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.injury import ElectricalStun
from wayfarer.engine.simulation.equipment.catalog import Armor
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Record

EVENT_PREFIX = "cattle-prod-affliction:"


class CattleProdResult(Record):
    outcome: Literal["unaffected", "resisted", "stunned"]
    target_id: str
    resistance: CheckTrace | None = None
    armor_bonus: int = Field(ge=0)
    contact_seconds: int = Field(ge=0)
    recovery_starts_turn: int | None = Field(default=None, ge=1)


def electrical_armor(armors: tuple[Armor, ...]) -> tuple[int, int, bool]:
    """Return damage DR and affliction bonus for a B432 surface shock.

    Metallic armor supplies DR 1 against nonlethal electrical damage but does
    not insulate the wearer. Nonmetallic DR protects normally and is doubled
    for the cattle prod's (0.5) linked affliction. Explicit insulation stops
    both parts of the shock.
    """

    if any(armor.electrical_conductivity == "insulated" for armor in armors):
        return max((armor.dr for armor in armors), default=0), 0, True
    nonmetallic = max(
        (armor.dr for armor in armors if armor.electrical_conductivity == "nonmetallic"),
        default=0,
    )
    metallic = any(armor.electrical_conductivity == "metallic" for armor in armors)
    return max(nonmetallic, int(metallic)), nonmetallic * 2, False


def resolve_cattle_prod(
    state: ResourceState,
    *,
    event_id: str,
    target_id: str,
    ht: int,
    armor_bonus: int,
    insulated: bool,
    contact_seconds: int,
    rng: RandomSource,
) -> tuple[ResourceState, CattleProdResult]:
    """Resolve and persist one linked effect inside the melee transaction."""

    persisted_id = EVENT_PREFIX + hashlib.sha256(event_id.encode()).hexdigest()
    previous = next((event for event in state.events if event.id == persisted_id), None)
    if previous is not None:
        return state, CattleProdResult.model_validate_json(previous.kind)
    if ht < 1 or armor_bonus < 0 or contact_seconds < 0:
        raise ValidationError("Cattle-prod resistance requires authoritative nonnegative facts")
    pool_id = "hp:" + target_id
    pool = next((candidate for candidate in state.pools if candidate.id == pool_id), None)
    if pool is None or pool.injury is None or pool.injury.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Cattle prod requires an explicit Basic Set injury pool")
    resistance = (
        None
        if insulated
        else success_roll(
            pool.injury.profile_id,
            ht - 3 + armor_bonus,
            check_modifiers(state, target_id, "ht", defensive=True),
            rng=rng,
        )
    )
    failed = resistance is not None and not resistance.outcome.succeeded
    recovery_starts = pool.injury.turn + contact_seconds + max(1, 20 - ht) if failed else None
    if failed:
        assert recovery_starts is not None
        electrical = ElectricalStun(
            source_id="equipment:cattle-prod",
            contact_ends_turn=pool.injury.turn + contact_seconds,
            recovery_starts_turn=recovery_starts,
        )
        pool = pool.model_copy(
            update={
                "injury": pool.injury.model_copy(
                    update={"stunned": True, "electrical_stun": electrical}
                )
            }
        )
    result = CattleProdResult(
        outcome="stunned" if failed else "unaffected" if insulated else "resisted",
        target_id=target_id,
        resistance=resistance,
        armor_bonus=armor_bonus,
        contact_seconds=contact_seconds,
        recovery_starts_turn=recovery_starts,
    )
    return (
        state.model_copy(
            update={
                "pools": tuple(
                    pool if candidate.id == pool_id else candidate for candidate in state.pools
                ),
                "events": state.events
                + (
                    ResourceEvent(
                        id=persisted_id,
                        at=state.game_time,
                        target_id=target_id,
                        kind=result.model_dump_json(),
                    ),
                ),
            }
        ),
        result,
    )
