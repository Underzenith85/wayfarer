"""Starting an encounter and seating its participants."""

from __future__ import annotations

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    StartBasicEncounter,
    StartEncounter,
    TakeCombatTurn,
    TakeUnarmedTurn,
    TypedCombatCommand,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter, basic_visible
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext, CoverSpatialFact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep, encounter_for


def _start_encounter(
    state: PlayState, command: StartEncounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    if any(e.id == command.encounter_id for e in state.encounters):
        raise ConflictError("Encounter ID already exists")
    actor_map = {actor.actor_id: actor for actor in state.actors}
    if len({p.actor_id for p in command.placements}) != len(command.placements) or any(
        p.actor_id not in actor_map for p in command.placements
    ):
        raise ValidationError("Encounter placements require unique play actors")
    initiatives: dict[str, int] = {}
    pools = {pool.id: pool for pool in state.resources.pools}
    for placement in command.placements:
        actor = actor_map[placement.actor_id]
        if engine.rules.gurps_equipment is not None:
            from wayfarer.engine.simulation.actors import fatigue_ready

            if not fatigue_ready(state, actor.actor_id):
                raise ValidationError("Exhausted actor cannot start combat")
        hp = pools.get(f"hp:{actor.actor_id}")
        if (
            actor.conditions
            or hp is None
            or (hp.injury.incapacitated if hp.injury else hp.current == 0)
        ):
            raise ValidationError("Incapacitated actor cannot start combat")
        build, _ = play.engine.reviewer.activate(
            actor.proposal,
            actor.approval,
            campaign_id=state.campaign_id,
            actor_id=actor.actor_id,
        )
        values = {value.target: int(value.value) for value in build.sheet.values}
        initiatives[actor.actor_id] = values["attribute:dx"]
    encounter = engine.start(
        command.encounter_id,
        command.battlefield_id,
        command.placements,
        initiatives,
        state.world,
        resources,
        frozenset(actor_map),
    )
    from wayfarer.engine.simulation.campaign.encounter_context import bind_scene

    if play.engine.rules.scenes is not None or command.scene_id is not None:
        encounter = bind_scene(encounter, play.engine.rules.scenes, engine.rules, command.scene_id)
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import bind_initial_hands

        encounter = bind_initial_hands(play.rules_context, state, encounter)
    from wayfarer.engine.simulation.combat.ranged.situation import declare

    encounter = declare(play.rules_context, encounter, command.ranged_situations)
    encounters = state.encounters + (encounter,)
    from wayfarer.engine.simulation.campaign.encounter_context import validate_contexts

    validate_contexts(
        state.model_copy(update={"encounters": encounters}),
        play.engine.rules.scenes,
        engine.rules,
    )
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.started",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
        available=engine.available(encounter, encounter.current_actor_id),
    )
    return CombatStep(state, encounter, resources, result)


def _start_basic_encounter(
    state: PlayState, command: StartBasicEncounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    if any(e.id == command.encounter_id for e in state.encounters):
        raise ConflictError("Encounter ID already exists")
    actor_map = {actor.actor_id: actor for actor in state.actors}
    if len(set(command.participant_ids)) != len(command.participant_ids) or any(
        actor_id not in actor_map for actor_id in command.participant_ids
    ):
        raise ValidationError("Basic encounter requires unique play actors")
    if any(
        fact.provenance.source != "scenario"
        or fact.provenance.source_id != command.scene_id
        or fact.provenance.declared_by != command.actor_id
        or fact.provenance.declared_revision != command.expected_revision
        or fact.provenance.invalidated_revision is not None
        for fact in command.facts
    ):
        raise ValidationError("Initial basic facts require scoped scenario provenance")
    initiatives: dict[str, int] = {}
    pools = {pool.id: pool for pool in state.resources.pools}
    for actor_id in command.participant_ids:
        actor = actor_map[actor_id]
        if engine.rules.gurps_equipment is not None:
            from wayfarer.engine.simulation.actors import fatigue_ready

            if not fatigue_ready(state, actor.actor_id):
                raise ValidationError("Exhausted actor cannot start combat")
        hp = pools.get(f"hp:{actor.actor_id}")
        if (
            actor.conditions
            or hp is None
            or (hp.injury.incapacitated if hp.injury else hp.current == 0)
        ):
            raise ValidationError("Incapacitated actor cannot start combat")
        build, _ = play.engine.reviewer.activate(
            actor.proposal,
            actor.approval,
            campaign_id=state.campaign_id,
            actor_id=actor.actor_id,
        )
        values = {value.target: int(value.value) for value in build.sheet.values}
        initiatives[actor.actor_id] = values["attribute:dx"]
    encounter = engine.start_basic(
        command.encounter_id,
        command.participant_ids,
        command.facts,
        initiatives,
        state.world,
        resources,
        frozenset(actor_map),
    )
    from wayfarer.engine.simulation.campaign.encounter_context import bind_scene

    encounter = bind_scene(encounter, play.engine.rules.scenes, engine.rules, command.scene_id)
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import bind_initial_hands

        encounter = bind_initial_hands(play.rules_context, state, encounter)
    from wayfarer.engine.simulation.combat.ranged.situation import declare

    encounter = declare(play.rules_context, encounter, command.ranged_situations)
    encounters = state.encounters + (encounter,)
    from wayfarer.engine.simulation.campaign.encounter_context import validate_contexts

    validate_contexts(
        state.model_copy(update={"encounters": encounters}),
        play.engine.rules.scenes,
        engine.rules,
    )
    return CombatStep(
        state,
        encounter,
        resources,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.started",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            available=engine.available(encounter, encounter.current_actor_id),
        ),
    )


def _prepare_encounter(
    state: PlayState, command: TypedCombatCommand, context: CombatContext
) -> Encounter:
    play = context.play
    engine = context.engine
    encounter = encounter_for(state, command.encounter_id)
    if play.engine.rules.scenes is not None:
        from wayfarer.engine.simulation.campaign.encounter_context import bind_scene

        encounter = bind_scene(encounter, play.engine.rules.scenes, engine.rules)
    if encounter.spatial_kind == "hex" and isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)):
        from wayfarer.orchestration.tactical_view import visible_actors

        if command.target_id is not None and command.target_id not in visible_actors(
            state, encounter, command.actor_id, board=play.rules_context.hex_map(encounter)
        ):
            raise ValidationError("Target is unavailable")
        if isinstance(command, TakeCombatTurn) and command.wait_trigger is not None:
            trigger = command.wait_trigger
            visible = visible_actors(
                state, encounter, command.actor_id, board=play.rules_context.hex_map(encounter)
            )
            if any(
                a is not None and a not in visible
                for a in (
                    trigger.actor_id,
                    trigger.target_id,
                    trigger.reaction_target_id,
                )
            ):
                raise ValidationError("Target is unavailable")
    elif encounter.spatial_kind == "basic" and isinstance(
        command, (TakeCombatTurn, TakeUnarmedTurn)
    ):
        spatial = encounter.spatial
        assert isinstance(spatial, BasicSpatialContext)
        if command.target_id is not None:
            if not basic_visible(encounter, command.actor_id, command.target_id):
                raise ValidationError("Target is unavailable")
            cover = spatial.active("cover", command.actor_id, command.target_id)
            if not isinstance(cover, CoverSpatialFact):
                raise ValidationError("Basic combat requires an authoritative cover fact")
            if cover.cover == "full":
                raise ValidationError("Full cover blocks the target")
        if isinstance(command, TakeCombatTurn) and command.wait_trigger is not None:
            for actor_id in (
                command.wait_trigger.actor_id,
                command.wait_trigger.target_id,
                command.wait_trigger.reaction_target_id,
            ):
                if actor_id is not None and not basic_visible(
                    encounter, command.actor_id, actor_id
                ):
                    raise ValidationError("Target is unavailable")
    from wayfarer.engine.simulation.combat.unarmed.fighters import guard_control

    guard_control(encounter, command, state)
    from wayfarer.engine.simulation.combat.tactical_transitions import prepare_defense

    if isinstance(command, ChooseDefense):
        if encounter.pending_unarmed is None and (
            command.parry_mode_id is not None or command.second_parry_mode_id is not None
        ):
            from wayfarer.engine.simulation.combat.melee.modes import mode
            from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode

            pending = encounter.pending_defense
            if (
                engine.rules.gurps_equipment is None
                or pending is None
                or pending.spell_cast_id is not None
            ):
                raise ValidationError(
                    "Explicit parry damage modes require a weapon or unarmed attack"
                )
            incoming = mode(
                play.rules_context,
                state,
                pending.attacker_id,
                pending.weapon_id,
                pending.mode_id,
            )
            if not isinstance(incoming, MeleeMode) and not (
                isinstance(incoming, RangedMode) and incoming.thrown
            ):
                raise ValidationError("Explicit parry damage modes require a parryable attack")
        encounter = prepare_defense(play.rules_context, state, encounter, command)
    return encounter
