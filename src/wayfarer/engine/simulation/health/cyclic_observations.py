"""Source-bound GM observations with real clock, resource and check evidence.

Elapsed time is the ordinary campaign clock; the observer attests continuity of
an authored procedure, while the engine independently spends its resources and
rolls its current approved skill. A statement of success cannot replace a roll.
"""

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.cyclic import CyclicAttack, require_cyclic_settled
from wayfarer.engine.rules.types.disease import ContactExposure
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready, level
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.cyclic import StopCyclic, save, stop
from wayfarer.engine.simulation.health.cyclic_contagion import ExposeCyclic, expose
from wayfarer.engine.simulation.health.cyclic_host_state import (
    AdjudicateCyclicLocationLoss,
    BeginCyclicProcedure,
    CompleteCyclicProcedure,
    CyclicBinding,
    CyclicHostCommand,
    CyclicHostReceipt,
    CyclicLocationAdjudication,
    ObserveCyclicExposure,
    ObserveCyclicStop,
    binding,
    history,
    save_receipt,
)
from wayfarer.engine.simulation.health.fright import maneuver_allowed
from wayfarer.engine.simulation.health.hit_locations import missing_location, part
from wayfarer.engine.simulation.resources import Consume, is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError

PROFILE = "gurps-basic-set-4e-2004"


def _retire_location(
    runtime: RulesContext,
    state: PlayState,
    command: AdjudicateCyclicLocationLoss,
    attack: CyclicAttack,
    source: CyclicBinding,
    *,
    principal_id: str,
) -> tuple[PlayState, CyclicHostReceipt]:
    """No printed automatic end is inferred from the loss of a delivery limb."""
    location = source.location
    if source.disease or location is None or part(location) not in {"arm", "hand", "leg", "foot"}:
        raise ValidationError("Cyclic location adjudication requires a bound delivery limb")
    build(runtime, state, command.actor_id, defensive=True)
    hp = next((p for p in state.resources.pools if p.id == "hp:" + command.actor_id), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != PROFILE:
        raise ValidationError("Cyclic location adjudication requires canonical current physiology")
    status = hp.injury
    if status.anatomy is None:
        raise ValidationError("Undeclared anatomy cannot establish loss of a bound Cyclic limb")
    if status.anatomy == "human" and not missing_location(status, location):
        raise ValidationError("The bound Cyclic limb remains applicable to the current anatomy")
    evidence = CyclicLocationAdjudication(
        campaign_policy=command.campaign_policy,
        source_id=source.source_id,
        source_revision=source.source_revision,
        location=location,
        anatomy=status.anatomy,
        evidence="severed-location" if status.anatomy == "human" else "incompatible-body",
        injury_ids=tuple(
            injury.id
            for injury in status.lasting_injuries
            if injury.kind == "severed"
            and (
                injury.location == location
                or injury.location.replace("arm", "hand").replace("leg", "foot") == location
            )
        ),
    )
    resources = save(state.resources, attack.model_copy(update={"active": False}))
    return state.model_copy(update={"resources": resources}), CyclicHostReceipt(
        command_id=command.id,
        kind="retired",
        occurrence_id=attack.id,
        actor_id=attack.actor_id,
        principal_id=principal_id,
        at=resources.game_time,
        location_id=command.location_id,
        reason=command.reason,
        adjudication=evidence,
    )


def _require_settled(state: PlayState, command: CyclicHostCommand) -> None:
    # Several due occurrences can share a now-missing limb. Retirement neither
    # advances time nor settles another occurrence, so each must be rescuable.
    if isinstance(command, AdjudicateCyclicLocationLoss):
        return
    require_cyclic_settled(state.resources.cyclic_attacks, state.resources.game_time + 1)


def _at(state: PlayState, actor_id: str, location_id: str) -> None:
    if actor_id not in {a.actor_id for a in state.actors}:
        raise ValidationError("Cyclic observation requires a campaign actor")
    entity = next((e for e in state.world.entities if e.id == actor_id), None)
    if entity is None or entity.location_id is None or entity.location_id != location_id:
        raise ValidationError("Cyclic observation requires current co-location")


def _active(state: PlayState, occurrence_id: str) -> tuple[CyclicAttack, CyclicBinding]:
    attack = next((a for a in state.resources.cyclic_attacks if a.id == occurrence_id), None)
    source = binding(state.resources, occurrence_id)
    if attack is None or not attack.active or source is None:
        raise ValidationError("Cyclic observation requires an active source-bound occurrence")
    if (
        source.actor_id != attack.actor_id
        or source.source_id != attack.attack_id
        or source.policy.condition != attack.stop_condition
    ):
        raise ValidationError("Cyclic occurrence and approved source policy disagree")
    return attack, source


def _performer(runtime: RulesContext, state: PlayState, actor_id: str) -> None:
    build(runtime, state, actor_id)
    hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
    if hp is None or hp.injury is None or hp.injury.incapacitated or hp.injury.stunned:
        raise ValidationError("Cyclic procedure requires a capable current performer")
    if not fatigue_ready(state, actor_id) or not maneuver_allowed(
        state.resources, actor_id, "ready"
    ):
        raise ValidationError("Cyclic procedure performer is currently incapacitated")
    require_hazard_capacity(state.resources, actor_id, "cyclic-procedure")


def _item(state: PlayState, source: CyclicBinding, actor_id: str, item_id: str | None) -> None:
    procedure = source.policy.procedure
    assert procedure is not None
    if procedure.consume_definition_id is None:
        if item_id is not None:
            raise ValidationError("Cyclic procedure does not use a consumable")
        return
    item = next((i for i in state.resources.items if i.id == item_id), None)
    if (
        item is None
        or item.owner_id != actor_id
        or not is_carried(state.resources, item)
        or item.definition_id != procedure.consume_definition_id
        or item.quantity < procedure.quantity
    ):
        raise ValidationError("Cyclic procedure requires its approved available resource")


def _procedure(
    runtime: RulesContext,
    state: PlayState,
    command: BeginCyclicProcedure | CompleteCyclicProcedure,
    source: CyclicBinding,
    *,
    principal_id: str,
) -> tuple[PlayState, CyclicHostReceipt]:
    procedure = source.policy.procedure
    if procedure is None:
        raise ValidationError("The approved Cyclic stopping condition has no timed procedure")
    records = history(state.resources)
    if isinstance(command, BeginCyclicProcedure):
        _at(state, command.performer_id, command.location_id)
        _performer(runtime, state, command.performer_id)
        _item(state, source, command.performer_id, command.item_id)
        if any(
            r.kind == "procedure-started"
            and r.performer_id == command.performer_id
            and not any(later.procedure_id == r.command_id for later in records)
            and any(a.id == r.occurrence_id and a.active for a in state.resources.cyclic_attacks)
            for r in records
        ):
            raise ConflictError("Cyclic performer already has an unfinished procedure")
        return state, CyclicHostReceipt(
            command_id=command.id,
            kind="procedure-started",
            occurrence_id=command.occurrence_id,
            actor_id=command.actor_id,
            performer_id=command.performer_id,
            item_id=command.item_id,
            principal_id=principal_id,
            at=state.resources.game_time,
            location_id=command.location_id,
            reason=command.reason,
        )
    started = next(
        (
            r
            for r in records
            if r.command_id == command.procedure_id and r.kind == "procedure-started"
        ),
        None,
    )
    if (
        started is None
        or started.occurrence_id != command.occurrence_id
        or started.actor_id != command.actor_id
        or started.location_id != command.location_id
        or started.performer_id is None
    ):
        raise ValidationError("Cyclic procedure evidence belongs to another occurrence or location")
    if any(r.procedure_id == started.command_id for r in records):
        raise ConflictError("Cyclic procedure attempt was already consumed")
    if state.resources.game_time < started.at + procedure.seconds:
        raise ConflictError("Cyclic procedure requires elapsed ordinary campaign time")
    performer = started.performer_id
    _at(state, performer, command.location_id)
    _performer(runtime, state, performer)
    _item(state, source, performer, started.item_id)
    current = build(runtime, state, performer)
    assert current.statistics is not None
    # Resolve all capability and check inputs before spending anything or rolling.
    target = None
    attribute = "iq" if procedure.check == "physician" else procedure.check
    if procedure.check == "physician":
        target = int(level(current, "skill:physician").value)
    elif procedure.check is not None:
        target = getattr(current.statistics, procedure.check)
    resources = state.resources
    if started.item_id is not None:
        resources = runtime.resources.for_world(state.world).apply(
            resources,
            Consume(
                id="cyclic-procedure-resource:" + command.id,
                actor_id=performer,
                expected_revision=resources.revision,
                item_id=started.item_id,
                quantity=procedure.quantity,
            ),
            system=True,
        )
    check = (
        None
        if target is None
        else success_roll(
            PROFILE,
            target,
            check_modifiers(resources, performer, attribute or "iq"),
            rng=runtime.rng,
        )
    )
    return state.model_copy(update={"resources": resources}), CyclicHostReceipt(
        command_id=command.id,
        kind="stopped" if check is None or check.outcome.succeeded else "procedure-failed",
        occurrence_id=command.occurrence_id,
        actor_id=command.actor_id,
        principal_id=principal_id,
        at=resources.game_time,
        location_id=command.location_id,
        reason=command.reason,
        performer_id=performer,
        item_id=started.item_id,
        procedure_id=started.command_id,
        check=check,
    )


def _exposure(
    runtime: RulesContext,
    state: PlayState,
    command: ObserveCyclicExposure,
    *,
    principal_id: str,
) -> tuple[PlayState, CyclicHostReceipt]:
    attack, source = _active(state, command.source_occurrence_id)
    _at(state, attack.actor_id, command.location_id)
    if attack.damage_type != "tox" or attack.contagious == "none":
        raise ValidationError("Cyclic source does not approve an infectious toxic attack")
    if source.disease and attack.cycle == 0 and not source.policy.infectious_during_incubation:
        raise ValidationError("Approved illness is not contagious during incubation")
    check_due = (state.resources.game_time // 86400 + 1) * 86400
    if (
        state.resources.game_time + attack.incubation_seconds < check_due
        and command.early_incubation_resolution is None
    ):
        raise ValidationError(
            "Incubation precedes daily resistance: GM must explicitly choose deferral to the daily check"
        )
    routes = {
        "blood": "blood-entry",
        "contact": "skin-contact",
        "digestive": "ingestion",
        "respiratory": "shared-air",
    }
    if attack.contagion_vector is None or routes[attack.contagion_vector] != command.route:
        raise ValidationError("Cyclic contact does not establish the approved illness vector")
    contacts = {
        "blood-entry": {"brief-touch", "prolonged-contact", "intimate-contact"},
        "skin-contact": {"brief-touch", "shared-material", "prolonged-contact", "intimate-contact"},
        "ingestion": {"cooked-flesh", "raw-flesh"},
        "shared-air": {
            "avoided",
            "shared-building",
            "close-conversation",
            "brief-touch",
            "shared-material",
            "prolonged-contact",
            "intimate-contact",
        },
    }
    if command.contact not in contacts[command.route]:
        raise ValidationError("Cyclic contact category does not fit the observed vector")
    precaution = next((p for p in source.policy.precautions if p.id == command.precaution_id), None)
    if (command.precaution_id is not None and precaution is None) or (
        command.precaution_id is None and command.precaution_understood
    ):
        raise ValidationError("Cyclic precaution requires the named source-approved facts")
    if precaution is not None and not command.precaution_understood:
        raise ValidationError("Cyclic protection requires observed understanding and proper use")
    current = build(runtime, state, command.actor_id, defensive=True)
    assert current.statistics is not None
    relationship = ContactExposure(
        id="cyclic-contact:" + command.id,
        actor_id=command.actor_id,
        disease_id=attack.attack_id,
        vector=attack.contagion_vector,
        contact=command.contact,
        occurred_at=state.resources.game_time,
        carrier_id=attack.actor_id,
        protection_id=command.precaution_id,
        protection_bonus=precaution.bonus if precaution else 0,
        protection_understood=command.precaution_understood,
    )
    resources = expose(
        state.resources,
        state.world,
        ExposeCyclic(
            id="cyclic-expose:" + command.id,
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            source_attack_id=attack.id,
            relationship_id=relationship.id,
        ),
        relationship,
        ht=current.statistics.ht,
        system=True,
    )
    exposure = next(e for e in resources.cyclic_exposures if e.relationship.id == relationship.id)
    return state.model_copy(update={"resources": resources}), CyclicHostReceipt(
        command_id=command.id,
        kind="exposed",
        occurrence_id=attack.id,
        actor_id=command.actor_id,
        principal_id=principal_id,
        at=resources.game_time,
        location_id=command.location_id,
        reason=command.reason,
        exposure_id=exposure.id,
        contact=command.contact,
        route=command.route,
        precaution_id=command.precaution_id,
        precaution_understood=command.precaution_understood,
        early_incubation_resolution=command.early_incubation_resolution,
    )


def resolve(
    runtime: RulesContext,
    state: PlayState,
    command: CyclicHostCommand,
    *,
    principal_id: str,
) -> tuple[PlayState, CyclicHostReceipt]:
    if runtime.reviewer.compiler.statistics_profile != PROFILE or state.lifecycle != "active":
        raise ValidationError("Cyclic observation requires an active Basic Set campaign")
    if state.revision != command.expected_revision:
        raise ConflictError("Cyclic observation revision changed")
    _require_settled(state, command)
    _at(state, command.actor_id, command.location_id)
    if isinstance(command, ObserveCyclicExposure):
        updated, result = _exposure(runtime, state, command, principal_id=principal_id)
    else:
        attack, source = _active(state, command.occurrence_id)
        if attack.actor_id != command.actor_id:
            raise ValidationError("Cyclic observation names another occurrence subject")
        if isinstance(command, AdjudicateCyclicLocationLoss):
            updated, result = _retire_location(
                runtime, state, command, attack, source, principal_id=principal_id
            )
        elif isinstance(command, ObserveCyclicStop):
            if source.policy.procedure is not None:
                raise ValidationError("Cyclic stopping requires the approved procedure evidence")
            updated = state
            result = CyclicHostReceipt(
                command_id=command.id,
                kind="stopped",
                occurrence_id=attack.id,
                actor_id=attack.actor_id,
                principal_id=principal_id,
                at=state.resources.game_time,
                location_id=command.location_id,
                reason=command.reason,
            )
        else:
            updated, result = _procedure(runtime, state, command, source, principal_id=principal_id)
        if result.kind == "stopped":
            resources = stop(
                updated.resources,
                StopCyclic(
                    id="cyclic-stop-observed:" + command.id,
                    actor_id=command.actor_id,
                    expected_revision=updated.resources.revision,
                    attack_id=attack.id,
                    condition=source.policy.condition,
                ),
                system=True,
            )
            updated = updated.model_copy(update={"resources": resources})
    resources = save_receipt(updated.resources, result).model_copy(
        update={"revision": state.resources.revision + 1}
    )
    return updated.model_copy(
        update={"revision": state.revision + 1, "resources": resources}
    ), result
