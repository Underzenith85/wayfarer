"""Source-referenced technology and multi-world campaign cases for issue #504."""

from dataclasses import replace
from decimal import Decimal

import pytest
from test_actions import engine, seed, world

from wayfarer.engine.character.compiler import (
    DerivedSheet,
    PurchasedEntry,
    ValidatedBuild,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules
from wayfarer.engine.simulation.campaign.world_context import (
    LOCAL_TECHNOLOGY_STEP_SECONDS,
    CompleteTechnologyProject,
    MaterialCost,
    Realm,
    StartTechnologyProject,
    TechnologyField,
    TechnologyPath,
    TechnologyProjectRule,
    TechnologyQualification,
    TravelBetweenWorlds,
    TravelLink,
    WorldContextRules,
    WorldContextState,
    apply_world_context,
    effective_technology_level,
    technology_field,
)
from wayfarer.engine.simulation.resources import Consume
from wayfarer.errors import ConflictError, ValidationError


def rules() -> WorldContextRules:
    return WorldContextRules(
        id="world-context",
        version=1,
        paths=(
            TechnologyPath(id="standard"),
            TechnologyPath(id="steam", divergence_level=5),
        ),
        realms=(
            Realm(
                id="home",
                kind="physical",
                baseline_technology_level=6,
                technology_fields=(
                    TechnologyField(field="communications", level=5),
                    TechnologyField(field="medicine", level=7, path_id="steam"),
                ),
                location_ids=("dock", "alley"),
            ),
            Realm(
                id="echo",
                kind="alternate",
                baseline_technology_level=8,
                location_ids=("far",),
            ),
        ),
        links=(
            TravelLink(
                id="gate",
                origin_realm_id="home",
                destination_realm_id="echo",
                origin_location_id="dock",
                destination_location_id="far",
                method_id="stone-gate",
                capability_id="world.gate",
                fatigue_cost_per_traveler=2,
                required_fact_ids=("clue",),
                required_definition_ids=("skill:observation",),
                required_item_definition_ids=("tool",),
            ),
            TravelLink(
                id="unknown-conveyor",
                origin_realm_id="home",
                destination_realm_id="echo",
                origin_location_id="dock",
                destination_location_id="far",
                method_id="unknown-conveyor",
                capability_id="world.parachronic-conveyor",
            ),
        ),
        technology_projects=(
            TechnologyProjectRule(
                id="raise-communications",
                realm_id="home",
                field="communications",
                from_level=5,
                to_level=6,
                duration_seconds=LOCAL_TECHNOLOGY_STEP_SECONDS,
                qualifications=(
                    TechnologyQualification(definition_id="skill:engineering", technology_level=5),
                    TechnologyQualification(definition_id="skill:physics", technology_level=6),
                ),
                material_costs=(MaterialCost(item_id="potions", quantity=2),),
            ),
        ),
        executable_capability_ids=("world.gate",),
    )


def configured() -> ActionEngine:
    base = engine()
    return ActionEngine(
        base.reviewer,
        base.resources,
        ActionRules(
            id="actions",
            version=1,
            checks=base.rules.checks,
            consumables=base.rules.consumables,
            world_context=rules(),
        ),
    )


def technology_build() -> ValidatedBuild:
    base = engine().reviewer.compiler.rules
    return ValidatedBuild(
        "technology-build",
        base,
        (
            PurchasedEntry("skill:engineering", 4, 4, 5),
            PurchasedEntry("skill:physics", 4, 4, 6),
        ),
        8,
        0,
        DerivedSheet(
            (
                DerivedValue("skill:engineering", Decimal(12), ()),
                DerivedValue("skill:physics", Decimal(12), ()),
            )
        ),
        "Ada",
        "",
    )


def test_split_and_divergent_technology_are_world_facts_not_catalog_mutations() -> None:
    context = rules()
    state = WorldContextState()
    assert effective_technology_level(context, state, "home") == 6
    assert effective_technology_level(context, state, "home", "communications") == 5
    assert technology_field(context, state, "home", "medicine") == TechnologyField(
        field="medicine", level=7, path_id="steam"
    )
    assert engine().resources.specs["tool"].technology_level == 0


def test_local_technology_consumes_materials_and_requires_two_year_project() -> None:
    reducer = engine()
    resources = seed(reducer).resources
    context, started_resources, unchanged_world, outcome = apply_world_context(
        WorldContextState(),
        resources,
        world(),
        StartTechnologyProject(
            id="local-tech",
            actor_id="a",
            expected_revision=0,
            rule_id="raise-communications",
            contributor_ids=("a",),
        ),
        rules(),
        builds={"a": technology_build()},
        consume=lambda current, command: reducer.resources.apply(
            current, command, system=True, rng=RecordedDice(())
        ),
        advance=lambda current, _to, _parent: current,
        system=True,
    )
    assert outcome.status == "started" and unchanged_world == world()
    assert next(item for item in started_resources.items if item.id == "potions").quantity == 3
    project = context.projects[0]
    assert project.due_at - project.started_at == LOCAL_TECHNOLOGY_STEP_SECONDS
    with pytest.raises(ConflictError, match="not yet due"):
        apply_world_context(
            context,
            started_resources,
            world(),
            CompleteTechnologyProject(
                id="finish-tech",
                actor_id="a",
                expected_revision=started_resources.revision,
                project_id="local-tech",
            ),
            rules(),
            builds={"a": technology_build()},
            consume=lambda current, _command: current,
            advance=lambda current, _to, _parent: current,
            system=True,
        )
    due_resources = started_resources.model_copy(update={"game_time": project.due_at})
    completed, final_resources, _, result = apply_world_context(
        context,
        due_resources,
        world(),
        CompleteTechnologyProject(
            id="finish-tech",
            actor_id="a",
            expected_revision=due_resources.revision,
            project_id="local-tech",
        ),
        rules(),
        builds={"a": technology_build()},
        consume=lambda current, _command: current,
        advance=lambda current, _to, _parent: current,
        system=True,
    )
    assert result.status == "completed"
    assert effective_technology_level(rules(), completed, "home", "communications") == 6
    replayed, replay_resources, _, replay = apply_world_context(
        completed,
        final_resources,
        world(),
        CompleteTechnologyProject(
            id="finish-tech",
            actor_id="a",
            expected_revision=due_resources.revision,
            project_id="local-tech",
        ),
        rules(),
        builds={"a": technology_build()},
        consume=lambda current, _command: current,
        advance=lambda current, _to, _parent: current,
        system=True,
    )
    assert replay.status == "completed" and replayed == completed
    assert replay_resources == final_resources


def test_world_transfer_is_atomic_replayable_and_preserves_inventory_and_knowledge() -> None:
    reducer = configured()
    state = seed(reducer)
    state = state.model_copy(
        update={
            "world": replace(state.world, knowledge=(("a", "clue"),)),
            "configuration_digest": reducer.digest,
        }
    )
    command = TravelBetweenWorlds(
        id="cross-gate",
        actor_id="a",
        expected_revision=0,
        link_id="gate",
        traveler_ids=("a",),
    )
    moved, outcome = reducer.campaign.apply(state, command, rng=RecordedDice(()), system=False)
    assert outcome.status == "arrived"
    assert next(item for item in moved.world.entities if item.id == "a").location_id == "far"
    assert moved.world.knowledge == state.world.knowledge
    assert moved.resources.items == state.resources.items
    assert next(pool for pool in moved.resources.pools if pool.id == "fp:a").current == 8
    assert moved.world_context.transfers[0].destination_realm_id == "echo"
    replayed, replay = reducer.campaign.apply(moved, command, rng=RecordedDice(()), system=False)
    assert replay.status == "arrived" and replayed == moved


def test_unsupported_travel_rejects_before_resources_are_spent() -> None:
    reducer = configured()
    state = seed(reducer)
    with pytest.raises(ValidationError, match="no registered executable capability"):
        reducer.campaign.apply(
            state,
            TravelBetweenWorlds(
                id="bad-transfer",
                actor_id="a",
                expected_revision=0,
                link_id="unknown-conveyor",
                traveler_ids=("a",),
            ),
            rng=RecordedDice(()),
        )
    assert state.resources.revision == 0


def test_invalid_local_technology_source_contract_fails_closed() -> None:
    with pytest.raises(ValueError, match="two years"):
        TechnologyProjectRule(
            **{
                **rules().technology_projects[0].model_dump(),
                "duration_seconds": 1,
            }
        )
    with pytest.raises(ValidationError, match="lower- and higher-TL"):
        reducer = engine()
        apply_world_context(
            WorldContextState(),
            seed(reducer).resources,
            world(),
            StartTechnologyProject(
                id="unqualified",
                actor_id="a",
                expected_revision=0,
                rule_id="raise-communications",
                contributor_ids=("a",),
            ),
            rules(),
            builds={"a": replace(technology_build(), purchases=())},
            consume=lambda current, command: reducer.resources.apply(current, command, system=True),
            advance=lambda current, _to, _parent: current,
            system=True,
        )


def test_material_callback_is_typed_as_resource_consume() -> None:
    assert Consume.model_fields["kind"].default == "consume"
