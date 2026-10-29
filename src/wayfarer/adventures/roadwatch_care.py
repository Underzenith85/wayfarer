"""Actual finite, zero-quality bandage provision for the authored clinic.

Recovery tasks reserve distinct wound uses durably when they start. Bandaging
followed by First Aid on the same wound shares one reservation; interrupted
procedures retain their reservation; this adapter does not claim that the core
inventory automatically depletes general medical equipment.
"""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
from wayfarer.engine.simulation.resources import is_carried
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical import CareEnvironment, CarePolicy
from wayfarer.orchestration.play import PlayService

SUPPLIES_ID = "bandages-post"
SUPPLIES_DEFINITION = "equipment:bandages"
PROVIDER_ID = "b"
CLINIC_ID = "shelter"


def environment(_play: PlayService, state: PlayState, target_id: str) -> CareEnvironment:
    """Real clinic context; ordinary FP rest never depends on bandage stock."""
    entities = {entity.id: entity for entity in state.world.entities}
    patient, station = entities.get(target_id), entities.get("aid-station")
    if (
        target_id not in {"a", "b"}
        or patient is None
        or station is None
        or patient.location_id != CLINIC_ID
        or station.location_id != CLINIC_ID
        or not any(
            fact.id == "aid-station-ready"
            and fact.subject_id == "aid-station"
            and fact.predicate == "care"
            for fact in state.world.facts
        )
    ):
        raise ValidationError("Roadside Watch recovery requires the authored shelter clinic")
    return CareEnvironment(technology_level=3, sterile=True, equipment_quality_modifier=0)


def admit(play: PlayService, state: PlayState, command: BeginRecovery) -> None:
    """Pure, procedure-aware reservations; the native task is the only write."""
    target_id = command.target_id or command.actor_id
    environment(play, state, target_id)
    if command.kind == "rest":
        if command.actor_id != target_id:
            raise ValidationError("Rest is personal recovery")
        return
    if command.kind not in {"bandage", "first-aid"} or command.actor_id != PROVIDER_ID:
        raise ValidationError(
            "Only Iven's bandaging and paid First Aid are admitted at this clinic"
        )
    provider = next((entity for entity in state.world.entities if entity.id == PROVIDER_ID), None)
    if provider is None or provider.location_id != CLINIC_ID:
        raise ValidationError("The actual caregiver must be present at the clinic")
    if command.wound_id is None:
        raise ValidationError("Clinic supplies must reserve an actual recorded wound")
    supplies = next((item for item in state.resources.items if item.id == SUPPLIES_ID), None)
    if (
        supplies is None
        or supplies.definition_id != SUPPLIES_DEFINITION
        or supplies.owner_id != PROVIDER_ID
        or supplies.quantity != 1
        or not is_carried(state.resources, supplies)
        or supplies.container_id is not None
    ):
        raise ValidationError("Adequate clinic bandages must actually be carried by Iven")
    supply_entity = next(
        (entity for entity in state.world.entities if entity.id == SUPPLIES_ID), None
    )
    if (
        supply_entity is None
        or supply_entity.kind is not EntityKind.OBJECT
        or supply_entity.owner_id != PROVIDER_ID
        or supply_entity.location_id not in (None, CLINIC_ID)
    ):
        raise ValidationError("Actual clinic bandage custody and location are inconsistent")
    if supplies.condition is not None and (
        supplies.condition.hp <= 0
        or supplies.condition.disabled
        or supplies.condition.destroyed
        or supplies.condition.burning
    ):
        raise ValidationError("Damaged or burning bandages are inadequate for clinic care")
    if not any(
        fact.id == "bandages-post-sterile"
        and fact.subject_id == SUPPLIES_ID
        and fact.predicate == "condition"
        and fact.value == "clean sterile bandages"
        for fact in state.world.facts
    ):
        raise ValidationError("Clinic bandage sterility is not established")
    spec = play.engine.resources.specs.get(SUPPLIES_DEFINITION)
    features = (
        [
            feature
            for feature in spec.general
            if feature.kind == "medical" and feature.skill_id == "skill:first-aid"
        ]
        if spec is not None
        else []
    )
    if len(features) != 1 or features[0].modifier != 0 or features[0].capacity != 6:
        raise ValidationError("Clinic requires the actual pinned basic-bandage specification")
    reserved_wounds = {
        task.wound_id
        for task in state.resources.recovery_tasks
        if task.kind in {"bandage", "first-aid"} and task.wound_id is not None
    }
    capacity = features[0].capacity
    if len(reserved_wounds | {command.wound_id}) > capacity:
        raise ValidationError("The six-wound bandage provision is exhausted")
    if command.kind == "first-aid" and not any(
        task.kind == "bandage"
        and task.target_id == target_id
        and task.wound_id == command.wound_id
        and task.status == "completed"
        and task.settled
        for task in state.resources.recovery_tasks
    ):
        raise ValidationError(
            "Complete bandaging this wound before the clinic's First Aid procedure"
        )


POLICY = CarePolicy(environment, admit, capture_bandage_environment=True)
