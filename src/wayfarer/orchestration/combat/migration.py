"""Moving an encounter between spatial representations."""

from __future__ import annotations

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    DeclareBasicSpatialFacts,
    MigrateEncounterBasic,
    MigrateEncounterHex,
    TypedCombatCommand,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext, BasicSpatialFact
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep


def _migrate(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, MigrateEncounterHex)
    from wayfarer.engine.simulation.combat.tactical_transitions import migrate

    encounter = migrate(play.rules_context, state, encounter, command)
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.hex_migrated",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _migrate_basic(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    assert isinstance(command, MigrateEncounterBasic)
    from wayfarer.engine.simulation.combat.tactical_transitions import migrate_basic

    encounter = migrate_basic(context.play.rules_context, state, encounter, command)
    return CombatStep(
        state,
        encounter,
        state.resources,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.basic_migrated",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
        ),
    )


def _declare_basic_facts(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    assert isinstance(command, DeclareBasicSpatialFacts)
    spatial = encounter.spatial
    if not isinstance(spatial, BasicSpatialContext) or encounter.status != "active":
        raise ValidationError("Basic spatial facts require an active basic encounter")
    if any(
        fact.provenance.source != "gm-adjudication"
        or fact.provenance.source_id != command.id
        or fact.provenance.declared_by != command.actor_id
        or fact.provenance.declared_revision != command.expected_revision
        or fact.provenance.invalidated_revision is not None
        or fact.subject_id not in encounter.turn_order
        or fact.object_id not in encounter.turn_order
        for fact in command.facts
    ):
        raise ValidationError("Basic spatial adjudication requires scoped GM provenance")

    def key(fact: BasicSpatialFact) -> tuple[str, str, str]:
        return (
            fact.kind,
            min(fact.subject_id, fact.object_id) if fact.kind == "distance" else fact.subject_id,
            max(fact.subject_id, fact.object_id) if fact.kind == "distance" else fact.object_id,
        )

    keys = {key(fact) for fact in command.facts}
    if len(keys) != len(command.facts):
        raise ValidationError("Basic spatial adjudication contains duplicate facts")
    retained = tuple(
        fact.model_copy(
            update={
                "provenance": fact.provenance.model_copy(
                    update={"invalidated_revision": command.expected_revision}
                )
            }
        )
        if fact.provenance.invalidated_revision is None and key(fact) in keys
        else fact
        for fact in spatial.facts
    )
    encounter = encounter.model_copy(
        update={"spatial_context": spatial.model_copy(update={"facts": retained + command.facts})}
    )
    context.engine.validate(
        encounter,
        state.world,
        state.resources,
        frozenset(actor.actor_id for actor in state.actors),
    )
    return CombatStep(
        state,
        encounter,
        state.resources,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.basic_spatial_facts_declared",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            available=context.engine.available(encounter, encounter.current_actor_id),
        ),
    )
