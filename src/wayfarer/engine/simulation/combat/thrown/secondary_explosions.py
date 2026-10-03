"""B136/B484 causal explosions from authoritative object damage results."""

import json

from wayfarer.engine.rules.types.explosion import ExplosionSpec
from wayfarer.engine.rules.types.object import ObjectResult
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts, save
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def schedule_secondary_object_blast(
    resources: ResourceState,
    encounter: Encounter,
    parent: BlastRecord,
    result: ObjectResult,
) -> ResourceState:
    if not result.exploded:
        return resources
    item = next(
        (i for i in (*resources.items, *resources.expended_items) if i.id == result.item_id), None
    )
    if (
        result not in resources.object_results
        or result.explosion_dice <= 0
        or not result.condition.destroyed
        or item is None
        or item.condition != result.condition
        or not any(b.id == parent.id and not b.resolved for b in blasts(resources))
    ):
        raise ValidationError("Secondary blast requires its actual current object damage result")
    # The primary warhead is already detonating; its own carrier cannot add
    # another explosion merely because the same detonation damaged that carrier.
    if item.id == parent.source_item_id and (parent.destroy_source or parent.follow_item):
        return resources
    point = item.ground
    if point is None:
        # deferred: causal record construction cannot eagerly import a RulesContext verb.
        from wayfarer.engine.simulation.combat.thrown.flight import position

        if item not in resources.items:
            raise ValidationError("Secondary blast requires the current mapped object position")
        owner = next((p for p in encounter.participants if p.actor_id == item.owner_id), None)
        if owner is None:
            raise ValidationError("Secondary blast requires the current mapped object position")
        point = position(encounter, owner)
    if point.encounter_id != encounter.id:
        raise ValidationError("Secondary blast object position belongs to another encounter")
    child_id = "fragile-object:" + result.command_id
    superseded = tuple(
        b
        for b in blasts(resources)
        if not b.resolved and b.source_item_id == item.id and b.id not in (parent.id, child_id)
    )
    for prior in superseded:
        resources = save(
            resources,
            prior.model_copy(
                update={
                    "resolved": True,
                    "evidence": json.dumps(
                        {
                            "superseded_by": child_id,
                            "object_damage_command_id": result.command_id,
                            "prior_evidence": prior.evidence,
                        },
                        sort_keys=True,
                    ),
                }
            ),
            result.command_id + ":supersede:" + prior.id,
        )
    child = BlastRecord(
        id=child_id,
        encounter_id=encounter.id,
        source_item_id=item.id,
        payload=ExplosionSpec(dice=result.explosion_dice),
        due=resources.game_time,
        center=point,
        deferred_ticks=sum(b.deferred_ticks for b in superseded),
        evidence=json.dumps(
            {
                "source": "B136/B484 Fragile (Explosive) object result",
                "parent_blast_id": parent.id,
                "object_damage_command_id": result.command_id,
            },
            sort_keys=True,
        ),
    )
    return save(resources, child, result.command_id + ":secondary-object-blast")
