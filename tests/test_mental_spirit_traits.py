"""Independent mental/spirit expectations, Characters 4e B40-161."""

import pytest
from test_statistics import gurps_draft
from trait_support import approved_build, options, trait_compiler

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.character.traits.mental_spirit import mental_spirit_traits
from wayfarer.engine.rules.supernatural import inventory
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mental_spirit import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.traits.mental_spirit import package as mental_spirit_package
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits.mental_spirit import (
    MentalChannel,
    MentalCommand,
    apply_mental_use,
    history,
)
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, ValidationError

EXPECTED = {
    "advantage:blessed": 10,
    "advantage:channeling": 10,
    "advantage:compartmentalized-mind": 50,
    "advantage:destiny": 5,
    "advantage:dominance": 20,
    "advantage:higher-purpose": 5,
    "advantage:illuminated": 15,
    "advantage:medium": 10,
    "advantage:mind-control": 50,
    "advantage:mind-probe": 20,
    "advantage:mind-reading": 30,
    "advantage:mind-shield": 4,
    "advantage:mindlink": 5,
    "advantage:modular-abilities": 10,
    "advantage:neutralize": 50,
    "advantage:oracle": 15,
    "advantage:possession": 100,
    "advantage:precognition": 25,
    "advantage:psi-static": 30,
    "advantage:psychometry": 20,
    "advantage:puppet": 5,
    "advantage:racial-memory": 15,
    "advantage:reawakened": 10,
    "advantage:special-rapport": 5,
    "advantage:spirit-empathy": 10,
    "advantage:super-luck": 100,
    "advantage:temporal-inertia": 15,
    "advantage:terror": 30,
    "advantage:true-faith": 15,
    "advantage:visualization": 10,
    "advantage:wild-talent": 20,
    "disadvantage:cursed": -75,
    "disadvantage:destiny": -5,
    "disadvantage:divine-curse": -5,
    "disadvantage:draining": -5,
    "disadvantage:dread": -10,
    "disadvantage:frightens-animals": -10,
    "disadvantage:infectious-attack": -5,
    "disadvantage:lifebane": -10,
    "disadvantage:revulsion": -5,
    "disadvantage:supernatural-features": -1,
    "disadvantage:supersensitive": -15,
    "disadvantage:uncontrollable-appetite": -15,
    "disadvantage:unique": -10,
    "disadvantage:weirdness-magnet": -15,
}


def compiler() -> CharacterCompiler:
    return trait_compiler(
        "mental-spirit-traits", PROFILE, mental_spirit_package(), hooks=RUNTIME_HOOKS
    )


def approved(*purchases: Purchase) -> tuple[ValidatedBuild, CharacterCompiler]:
    return approved_build(compiler(), *purchases)


def test_registry_and_inventory_account_for_all_45_distinct_entries() -> None:
    assert {binding.id: binding.point_cost for binding in BINDINGS} == EXPECTED
    rows = {row.id: row for row in inventory().entries if row.id in EXPECTED}
    assert set(rows) == set(EXPECTED)
    assert all(row.blockers == () for row in rows.values())
    assert all(row.evidence == ("tests/test_mental_spirit_traits.py",) for row in rows.values())


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [
        (Purchase(definition_id="advantage:blessed", trait=options(kind="very-blessed")), 20),
        (Purchase(definition_id="advantage:mindlink", trait=options(**{"group-size": 99})), 20),
        (
            Purchase(
                definition_id="advantage:modular-abilities",
                trait=options(framework="cosmic-power", capacity=5, slots=1),
            ),
            50,
        ),
        (Purchase(definition_id="advantage:racial-memory", trait=options(active=True)), 40),
        (Purchase(definition_id="disadvantage:divine-curse", trait=options(value=30)), -30),
        (Purchase(definition_id="disadvantage:dread", trait=options(rarity="common")), -15),
        (
            Purchase(
                definition_id="disadvantage:uncontrollable-appetite",
                trait=options(**{"self-control": 6}),
            ),
            -30,
        ),
    ],
)
def test_variable_costs_are_selected_by_trusted_parameters(
    purchase: Purchase, expected: int
) -> None:
    build, _ = approved(purchase)
    assert (
        next(
            value.cost for value in build.purchases if value.definition_id == purchase.definition_id
        )
        == expected
    )


def test_missing_unknown_and_mutually_exclusive_options_fail_closed() -> None:
    engine = compiler()
    for purchase in (
        Purchase(definition_id="advantage:blessed"),
        Purchase(definition_id="advantage:mindlink", trait=options(**{"group-size": 4})),
        Purchase(
            definition_id="advantage:mind-control",
            trait=TraitOptions(modifiers=("conditioning", "conditioning-only")),
        ),
    ):
        result = engine.compile(gurps_draft(purchase))
        assert result.build is None and "trait.invalid" in {
            error.code for error in result.diagnostics
        }


def test_projection_supplies_concrete_mental_and_spirit_capabilities() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:mind-shield", amount=4),
        Purchase(definition_id="advantage:compartmentalized-mind", amount=2),
        Purchase(definition_id="advantage:higher-purpose", amount=3),
        Purchase(definition_id="advantage:terror", trait=options(penalty=2)),
        Purchase(definition_id="advantage:medium"),
        Purchase(definition_id="advantage:precognition"),
        Purchase(definition_id="advantage:psi-static"),
    )
    traits = mental_spirit_traits(build, engine.definitions)
    assert traits.mind_shield_bonus() == 4
    assert traits.simultaneous_concentrations() == 3
    assert traits.higher_purpose_bonus(applies=True) == 3
    assert traits.higher_purpose_bonus(applies=False) == 0
    assert traits.terror_penalty() == -2
    assert traits.can_contact("spirits") and traits.can_contact("future")
    assert traits.resists("psi") and not traits.resists("magic")


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("a", EntityKind.ACTOR, "Reader", "room"),
            Entity("b", EntityKind.ACTOR, "Subject", "room"),
        ),
        facts=(Fact("surface", "b", "thought", "door"), Fact("secret", "b", "thought", "key")),
        knowledge=(("b", "surface"), ("b", "secret")),
    )


def command(kind: str = "activate", revision: int = 0, identifier: str = "read") -> MentalCommand:
    return MentalCommand.model_validate(
        {
            "id": f"mental-{kind}-{revision}",
            "actor_id": "a",
            "expected_revision": revision,
            "definition_id": "advantage:mind-reading",
            "channel_id": identifier,
            "kind": kind,
        }
    )


def channel(**changes: object) -> MentalChannel:
    return MentalChannel(
        id="read",
        definition_id="advantage:mind-reading",
        actor_id="a",
        target_id="b",
        location_id="room",
        kind="read",
        fact_ids=("surface",),
        actor_score=14,
        actor_roll=10,
        resistance_score=12,
        resistance_roll=11,
        duration_seconds=60,
        fatigue_cost=2,
    ).model_copy(update=changes)


def test_success_spends_cost_reveals_only_authored_facts_and_restarts() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mind-reading"))
    resources = ResourceState(pools=(Pool(id="fp:a", current=10, maximum=10),))
    state, learned, result = apply_mental_use(
        resources,
        world(),
        command(),
        build,
        engine.definitions,
        (channel(),),
        authorized_actor_id="a",
        system=True,
    )
    assert result.outcome == "successful" and result.revealed_fact_ids == ("surface",)
    assert state.pools[0].current == 8
    assert ("a", "surface") in learned.knowledge and ("a", "secret") not in learned.knowledge
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert history(restarted)[0].outcome == result
    assert apply_mental_use(
        restarted,
        learned,
        command(),
        build,
        engine.definitions,
        (channel(),),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, learned, result)


def test_resistance_blocking_authority_and_compare_and_set_fail_closed() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mind-reading"))
    resources = ResourceState(pools=(Pool(id="fp:a", current=10, maximum=10),))
    _, _, resisted = apply_mental_use(
        resources,
        world(),
        command(),
        build,
        engine.definitions,
        (channel(actor_roll=13, resistance_roll=8),),
        authorized_actor_id="a",
        system=True,
    )
    assert resisted.outcome == "resisted"
    _, _, blocked = apply_mental_use(
        resources,
        world(),
        command(),
        build,
        engine.definitions,
        (channel(blocked=True),),
        authorized_actor_id="a",
        system=True,
    )
    assert blocked.outcome == "blocked"
    with pytest.raises(ValidationError, match="authority"):
        apply_mental_use(
            resources,
            world(),
            command(),
            build,
            engine.definitions,
            (channel(),),
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError, match="revision"):
        apply_mental_use(
            resources.model_copy(update={"revision": 1}),
            world(),
            command(),
            build,
            engine.definitions,
            (channel(),),
            authorized_actor_id="a",
            system=True,
        )


def test_persistent_control_can_be_interrupted_before_expiry_once() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mind-control"))
    control = channel(
        id="control",
        definition_id="advantage:mind-control",
        kind="influence",
        fact_ids=(),
        fatigue_cost=0,
    )
    activate = command(identifier="control").model_copy(
        update={"definition_id": "advantage:mind-control"}
    )
    state, current_world, result = apply_mental_use(
        ResourceState(),
        world(),
        activate,
        build,
        engine.definitions,
        (control,),
        authorized_actor_id="a",
        system=True,
    )
    assert result.effect_id in state.active_effect_ids
    assert state.scheduled == () and result.expires_at is None
    stop = MentalCommand(
        id="stop-control",
        actor_id="a",
        expected_revision=1,
        definition_id="advantage:mind-control",
        channel_id="control",
        kind="interrupt",
    )
    stopped, _, outcome = apply_mental_use(
        state,
        current_world,
        stop,
        build,
        engine.definitions,
        (control,),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "interrupted" and result.effect_id not in stopped.active_effect_ids
    assert stopped.scheduled == ()


@pytest.mark.parametrize(
    ("actor", "roll", "resister", "resist_roll", "scope", "expected", "effective"),
    [
        # B349: 30 becomes 16 against Will 15; 4 loses to 7.
        (30, 12, 15, 8, {}, "resisted", 16),
        (30, 12, 15, 11, {}, "resisted", 16),  # ties favor the subject (B348)
        (30, 12, 15, 12, {}, "successful", 16),
        (30, 12, 20, 8, {}, "resisted", 20),  # higher actual resistance sets the cap
        (14, 10, 12, 11, {}, "successful", 14),
        (10, 12, 8, 15, {}, "resisted", 10),  # attack must succeed, even if both fail
        (30, 17, 15, 18, {}, "resisted", 16),  # automatic failure beats raw margin
        (30, 18, 15, 18, {}, "resisted", 16),  # critical failure cannot affect target
        (30, 12, 15, 17, {}, "successful", 16),  # defender critical failure
        (16, 6, 20, 8, {}, "resisted", 16),  # critical success does not trump margins
        (10, 10, 1, 4, {}, "successful", 10),  # defender critical success uses its margin
        (30, 12, 15, 8, {"supernatural_attack": False}, "successful", 30),
        (30, 12, 15, 8, {"target_living_or_sapient": False}, "successful", 30),
        (30, 17, 15, 18, {"supernatural_attack": False}, "resisted", 30),
    ],
)
def test_source_resistance_changes_persistent_state_and_replays(
    actor: int,
    roll: int,
    resister: int,
    resist_roll: int,
    scope: dict[str, bool],
    expected: str,
    effective: int,
) -> None:
    from wayfarer.engine.rules.gurps_checks import replay_resistance

    build, engine = approved(Purchase(definition_id="advantage:mind-control"))
    authored = channel(
        definition_id="advantage:mind-control",
        kind="influence",
        fact_ids=(),
        actor_score=actor,
        actor_roll=roll,
        resistance_score=resister,
        resistance_roll=resist_roll,
        fatigue_cost=0,
        **scope,
    )
    use = command().model_copy(update={"definition_id": "advantage:mind-control"})
    initial = ResourceState(pools=(Pool(id="fp:a", current=10, maximum=10),))
    state, current_world, outcome = apply_mental_use(
        initial,
        world(),
        use,
        build,
        engine.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == expected
    assert state.pools[0].current == 10 and state.revision == 1
    assert current_world == world()
    assert outcome.resistance is not None
    assert outcome.resistance.attacker.effective_target == effective
    assert outcome.resistance.attacker.total == roll
    assert outcome.resistance.resister.total == resist_roll
    assert replay_resistance(outcome.resistance) == outcome.resistance
    if expected == "successful":
        assert outcome.effect_id in state.active_effect_ids
        assert state.scheduled == () and outcome.expires_at is None
    else:
        assert outcome.effect_id is None and outcome.expires_at is None
        assert state.active_effect_ids == () and state.scheduled == ()
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert history(restarted)[0].outcome == outcome
    assert apply_mental_use(
        restarted,
        current_world,
        use,
        build,
        engine.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, current_world, outcome)
    with pytest.raises(ConflictError, match="already used"):
        apply_mental_use(
            restarted,
            current_world,
            use.model_copy(update={"kind": "interrupt"}),
            build,
            engine.definitions,
            (authored,),
            authorized_actor_id="a",
            system=True,
        )


def test_failed_read_never_reveals_authored_facts() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mind-reading"))
    resources = ResourceState(pools=(Pool(id="fp:a", current=10, maximum=10),))
    state, learned, outcome = apply_mental_use(
        resources,
        world(),
        command(),
        build,
        engine.definitions,
        (channel(actor_score=30, actor_roll=12, resistance_score=15, resistance_roll=8),),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "resisted" and outcome.revealed_fact_ids == ()
    assert learned == world() and state.active_effect_ids == () and state.scheduled == ()


def test_unopposed_authored_check_still_requires_success() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mind-reading"))
    resources = ResourceState(pools=(Pool(id="fp:a", current=10, maximum=10),))
    _, learned, outcome = apply_mental_use(
        resources,
        world(),
        command(),
        build,
        engine.definitions,
        (channel(actor_score=10, actor_roll=12, resistance_score=None, resistance_roll=None),),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "resisted" and outcome.resistance is None
    assert learned == world()


def test_mind_control_changes_real_campaign_authorization_and_source_linger() -> None:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.campaign.access import CampaignMember
    from wayfarer.errors import AuthorizationError
    from wayfarer.orchestration.membership import require_control

    build, engine = approved(Purchase(definition_id="advantage:mind-control"))
    authored = channel(
        definition_id="advantage:mind-control",
        kind="influence",
        fatigue_cost=0,
        fact_ids=(),
        duration_seconds=1,
    )
    use = command().model_copy(update={"definition_id": authored.definition_id})
    controller = CampaignMember(principal_id="player-a", role="player", actor_ids=("a",))
    owner = CampaignMember(principal_id="player-b", role="player", actor_ids=("b",))
    play = PlayState(
        campaign_id="campaign",
        configuration_digest="test",
        world=world(),
        resources=ResourceState(),
        actors=(),
        members=(controller, owner),
    )
    with pytest.raises(AuthorizationError):
        require_control(controller, "b", play)
    require_control(owner, "b", play)
    state, current_world, result = apply_mental_use(
        play.resources,
        play.world,
        use,
        build,
        engine.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    controlled = play.model_copy(update={"resources": state, "world": current_world})
    require_control(controller, "b", controlled)
    from wayfarer.orchestration.pipeline import Controls

    Controls(controller, "b", state=controlled)("player-a")
    with pytest.raises(AuthorizationError):
        Controls(controller, "b", state=controlled)("player-b")
    with pytest.raises(AuthorizationError):
        require_control(owner, "b", controlled)
    assert current_world.knowledge == play.world.knowledge
    assert result.expires_at is None and state.scheduled == ()
    stopped, _, lingering = apply_mental_use(
        state.model_copy(update={"game_time": 20}),
        current_world,
        use.model_copy(update={"id": "stop", "kind": "stop-concentrating", "expected_revision": 1}),
        build,
        engine.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert lingering.outcome == "lingering" and lingering.expires_at == 200
    persisted = PlayState.model_validate_json(
        controlled.model_copy(update={"resources": stopped}).model_dump_json()
    )
    require_control(controller, "b", persisted)
    expired = persisted.model_copy(
        update={"resources": stopped.model_copy(update={"game_time": 200})}
    )
    require_control(owner, "b", expired)
    with pytest.raises(AuthorizationError):
        require_control(controller, "b", expired)


def test_possession_transfers_host_control_and_disables_previous_body() -> None:
    from wayfarer.engine.simulation.traits.mental_control import controlling_actor

    build, engine = approved(Purchase(definition_id="advantage:possession"))
    authored = channel(
        definition_id="advantage:possession",
        kind="possession",
        fatigue_cost=0,
        fact_ids=(),
        touching_target=True,
    )
    use = command().model_copy(update={"definition_id": authored.definition_id})
    state, current_world, result = apply_mental_use(
        ResourceState(),
        world(),
        use,
        build,
        engine.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert controlling_actor(state, "b") == "a"
    assert controlling_actor(state, "a") is None
    assert result.expires_at is None and not state.scheduled
    assert current_world.knowledge == world().knowledge
    with pytest.raises(ValidationError, match="another living host"):
        apply_mental_use(
            state,
            current_world,
            use.model_copy(update={"id": "leave", "kind": "interrupt", "expected_revision": 1}),
            build,
            engine.definitions,
            (authored,),
            authorized_actor_id="a",
            system=True,
        )
    resisted, _, result = apply_mental_use(
        ResourceState(),
        world(),
        use,
        build,
        engine.definitions,
        (authored.model_copy(update={"actor_roll": 16}),),
        authorized_actor_id="a",
        system=True,
    )
    assert result.outcome == "resisted" and controlling_actor(resisted, "b") == "b"
    assert controlling_actor(resisted, "a") == "a"
