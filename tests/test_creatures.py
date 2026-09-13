"""Independent Campaigns fourth-printing creature cases, B455-460."""

import pytest
from pydantic import ValidationError as SchemaError
from test_statistics import BASIC, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.character.creatures import CreatureCatalog
from wayfarer.engine.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    PROTOTYPE_PACKAGE,
    CampaignPolicy,
    CampaignRules,
    PackagePin,
    RulesCatalog,
)
from wayfarer.engine.rules.creatures import (
    command_training_days,
    representative_creatures,
    training_days,
)
from wayfarer.engine.rules.types.creature import CreatureVariation
from wayfarer.engine.simulation.creatures import (
    CompleteCreatureTraining,
    DirectCreature,
    SetCreatureRelationship,
    StartCreatureTraining,
    apply_creature_command,
)
from wayfarer.engine.simulation.movement.creature_mounts import mount_transport
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError


def creature_compiler() -> CharacterCompiler:
    package = profile_package(BASIC)
    policy = CampaignPolicy(
        id="policy:creature-test",
        version=1,
        point_budget=10000,
        disadvantage_limit=10000,
        attribute_ceiling=100,
        skill_ceiling=100,
        permitted_sources=frozenset(source.id for source in package.sources),
    )
    rules = CampaignRules(
        edition=package.edition,
        packages=(PackagePin(package.id, package.version, package.digest),),
        policy_id=policy.id,
        policy_version=policy.version,
    )
    return CharacterCompiler(RulesCatalog((package,)), rules, policy, statistics_profile=BASIC)


def catalog() -> CreatureCatalog:
    return CreatureCatalog(representative_creatures(), creature_compiler())


def resource_engine() -> ResourceEngine:
    world = World(
        entities=tuple(
            Entity(identifier, EntityKind.ACTOR, name)
            for identifier, name in (
                ("handler", "Handler"),
                ("new-handler", "New Handler"),
                ("dog", "Dog"),
                ("horse", "Horse"),
            )
        )
    )
    return ResourceEngine(
        world, RulesCatalog((PROTOTYPE_PACKAGE,)), DEFAULT_RULES, DEFAULT_POLICY, ()
    )


def test_representative_rows_compile_deterministically_through_character_engine() -> None:
    entries = catalog()
    assert set(entries.templates) == {
        "creature:house-cat",
        "creature:large-guard-dog",
        "creature:timber-wolf",
        "creature:cavalry-horse",
        "creature:draft-horse",
        "creature:basilisk",
        "creature:gryphon",
    }
    for index, template in enumerate(entries.templates.values()):
        first = entries.compile(f"actor-{index}", template.name, template.id)
        second = entries.compile(f"actor-{index}", template.name, template.id)
        assert first == second
        assert first.creature.build_revision
        assert first.creature.template_digest
        assert all(
            entry.reference == template.reference for entry in first.creature.point_provenance
        )
        assert {entry.origin for entry in first.creature.point_provenance} == {"template"}


def test_individual_variation_is_separate_and_keeps_point_origin() -> None:
    entries = catalog()
    ordinary = entries.compile("horse", "Comet", "creature:cavalry-horse").creature
    swift = entries.compile(
        "horse",
        "Comet",
        "creature:cavalry-horse",
        CreatureVariation(st=23, ground_move=9),
    ).creature
    assert ordinary.template_digest == swift.template_digest
    assert ordinary.individual_digest != swift.individual_digest
    assert (swift.statistics.st, swift.statistics.move().ordinary_move) == (23, 9)
    origins = {entry.definition_id: entry.origin for entry in swift.point_provenance}
    assert origins["attribute:st"] == origins["secondary:basic-move"] == "individual"
    assert origins["attribute:dx"] == "template"
    with pytest.raises(ValidationError, match="mentality interchange"):
        entries.compile(
            "wolf", "Wolf", "creature:timber-wolf", CreatureVariation(mentality="domestic")
        )


def test_selected_training_table_and_unsupported_cells() -> None:
    assert {
        (iq, level): training_days(iq, level) for iq in range(2, 6) for level in range(2, iq + 1)
    } == {
        (2, 2): 60,
        (3, 2): 30,
        (3, 3): 360,
        (4, 2): 7,
        (4, 3): 180,
        (4, 4): 360,
        (5, 2): 2,
        (5, 3): 90,
        (5, 4): 180,
        (5, 5): 720,
    }
    assert [command_training_days(iq) for iq in (3, 4, 5)] == [90, 30, 14]
    with pytest.raises(ValidationError, match="impossible"):
        training_days(3, 4)
    with pytest.raises(ValidationError, match="cannot learn"):
        command_training_days(2)


def test_wild_training_records_the_selected_source_penalty() -> None:
    engine = resource_engine()
    wolf = catalog().compile("dog", "Wolf", "creature:timber-wolf").creature
    wolf = wolf.model_copy(update={"handler_id": "handler"})
    state = apply_creature_command(
        engine,
        ResourceState(creatures=(wolf,)),
        StartCreatureTraining(
            id="train-wolf",
            actor_id="handler",
            expected_revision=0,
            creature_id="dog",
            training_id="wolf-level-2",
            training_kind="general",
            handler_skill_id="skill:animal-handling-canines",
            competence=12,
            target_level=2,
        ),
        system=True,
    )
    assert state.creatures[0].training is not None
    assert state.creatures[0].training.handling_modifier == -5


def test_narration_cannot_grant_an_unlearned_command_and_training_persists() -> None:
    engine = resource_engine()
    dog = catalog().compile("dog", "Ash", "creature:large-guard-dog").creature
    state = ResourceState(creatures=(dog,))
    relationship = SetCreatureRelationship(
        id="relationship",
        actor_id="handler",
        expected_revision=0,
        creature_id="dog",
        owner_id="handler",
        handler_id="handler",
    )
    state = apply_creature_command(engine, state, relationship, system=True)
    with pytest.raises(ValidationError, match="has not learned"):
        apply_creature_command(
            engine,
            state,
            DirectCreature(
                id="narrated-guard",
                actor_id="handler",
                expected_revision=1,
                creature_id="dog",
                command_id="guard",
            ),
        )
    general = StartCreatureTraining(
        id="start-general",
        actor_id="handler",
        expected_revision=1,
        creature_id="dog",
        training_id="general-4",
        training_kind="general",
        handler_skill_id="skill:animal-handling-dogs",
        competence=13,
        target_level=4,
    )
    state = apply_creature_command(engine, state, general, system=True)
    assert state.creatures[0].training is not None
    due = state.creatures[0].training.due_at
    with pytest.raises(ConflictError, match="deadline"):
        apply_creature_command(
            engine,
            state,
            CompleteCreatureTraining(
                id="finish-too-early",
                actor_id="handler",
                expected_revision=2,
                creature_id="dog",
                training_id="general-4",
            ),
            system=True,
        )
    state = state.model_copy(update={"game_time": due})
    state = apply_creature_command(
        engine,
        state,
        CompleteCreatureTraining(
            id="finish-general",
            actor_id="handler",
            expected_revision=2,
            creature_id="dog",
            training_id="general-4",
        ),
        system=True,
    )
    direct = DirectCreature(
        id="direct-guard",
        actor_id="handler",
        expected_revision=3,
        creature_id="dog",
        command_id="guard",
    )
    directed = apply_creature_command(engine, state, direct)
    assert directed.creatures[0].last_command_id == "guard"
    assert (
        next(
            entry.competence
            for entry in directed.creatures[0].learned_commands
            if entry.id == "guard"
        )
        == 13
    )
    restored = ResourceState.model_validate_json(directed.model_dump_json())
    assert restored == directed
    assert apply_creature_command(engine, restored, direct) == restored


def test_mount_capabilities_are_creature_facts_not_handler_or_transport_facts() -> None:
    engine = resource_engine()
    horse = catalog().compile("horse", "Banner", "creature:cavalry-horse").creature
    state = ResourceState(creatures=(horse,))
    state = apply_creature_command(
        engine,
        state,
        SetCreatureRelationship(
            id="first-handler",
            actor_id="handler",
            expected_revision=0,
            creature_id="horse",
            owner_id="handler",
            handler_id="handler",
        ),
        system=True,
    )
    before = state.creatures[0].mount
    state = apply_creature_command(
        engine,
        state,
        SetCreatureRelationship(
            id="new-handler",
            actor_id="handler",
            expected_revision=1,
            creature_id="horse",
            owner_id="new-handler",
            handler_id="new-handler",
        ),
        system=True,
    )
    after = state.creatures[0].mount
    assert before == after and after is not None and after.war_trained
    transport = mount_transport(state.creatures[0], transport_id="ride", rider_id="new-handler")
    assert (transport.acceleration, transport.top_speed, transport.footprint) == (8, 16, (-1, 0, 1))


def test_closed_creature_schemas_reject_unknown_behavior_and_bad_mounts() -> None:
    template = representative_creatures()[0]
    with pytest.raises(SchemaError):
        CreatureVariation.model_validate({"narrated_command": "attack"})
    with pytest.raises(SchemaError, match="riding mount"):
        type(template).model_validate(
            {
                **template.model_dump(),
                "mount": {"riding": False, "war_trained": True, "combat_training_years": 1},
            }
        )
    # Catalog identity is insensitive to caller iteration and contains only trusted templates.
    forward = CreatureCatalog(representative_creatures(), creature_compiler())
    reverse = CreatureCatalog(tuple(reversed(representative_creatures())), creature_compiler())
    assert forward.digest == reverse.digest
