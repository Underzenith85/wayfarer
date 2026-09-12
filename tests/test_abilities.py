"""Independent B46/48/61/69/106/111/366 expected results.

Characters third printing, Campaigns fourth printing; baseline delta pending.
No expected numbers are calculated using the implementation being tested.
"""

from dataclasses import replace

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.supernatural.abilities import PROFILE, validate_binding
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.abilities import (
    AbilityContext,
    apply_ability,
    damage_resistance,
    effects,
)
from wayfarer.engine.simulation.ability_types import AbilityChannel, AbilityCommand, AbilitySpec
from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, ValidationError


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("a", EntityKind.ACTOR, "Reader", "room"),
            Entity("b", EntityKind.ACTOR, "Subject", "room"),
        ),
        facts=(
            Fact("visible", "b", "visible", "person"),
            Fact("presence", "b", "detected", "present"),
            Fact("direction", "b", "direction", "north"),
            Fact("distance", "b", "distance", "one yard"),
            Fact("analysis", "b", "analysis", "gold"),
            Fact("thought", "b", "surface-thought", "I am hungry"),
            Fact("secret", "b", "password", "never revealed"),
        ),
        knowledge=(("a", "visible"), ("b", "secret")),
    )


def resources() -> ResourceState:
    return ResourceState(
        owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100)),
        pools=tuple(
            Pool(id="hp:" + actor, current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE))
            for actor in ("a", "b")
        ),
    )


def spec(kind: str = "mind-reading", modifiers: tuple[str, ...] = ()) -> AbilitySpec:
    return AbilitySpec.model_validate(
        {"definition_id": "trait:ability", "kind": kind, "modifiers": modifiers}
    )


def context(ability: AbilitySpec, level: int = 1) -> AbilityContext:
    return AbilityContext(
        PROFILE,
        level,
        TraitOptions(modifiers=ability.modifiers),
        14,
        14,
        14,
        10,
        channel=AbilityChannel(
            id="channel",
            ability_id=ability.definition_id,
            actor_id="a",
            target_id="b",
            location_id="room",
            presence_fact_ids=("presence",),
            detection_fact_ids=("direction",),
            precise_fact_ids=("distance",),
            analysis_fact_ids=("analysis",),
            thought_fact_ids=("thought",),
        ),
    )


def command(revision: int = 0, kind: str = "activate") -> AbilityCommand:
    return AbilityCommand.model_validate(
        dict(
            id=f"command-{revision}",
            actor_id="a",
            expected_revision=revision,
            kind=kind,
            ability_id="trait:ability",
            channel_id="channel",
        )
    )


def resolve(
    ability: AbilitySpec, ctx: AbilityContext, dice: list[int]
) -> tuple[ResourceState, World, str]:
    started, state_world, pending = apply_ability(
        resources(), world(), command(), ability, ctx, rng=RecordedDice([]), system=True
    )
    assert pending.outcome == "concentrating"
    assert not pending.revealed_fact_ids
    updated, state_world, result = apply_ability(
        started.model_copy(update={"game_time": 1}),
        state_world,
        command(1, "resolve"),
        ability,
        ctx,
        rng=RecordedDice(dice),
        system=True,
    )
    return updated, state_world, result.outcome


@pytest.mark.parametrize(
    "kind,level,modifiers,expected",
    [
        ("burning-malediction", 2, ("malediction-1",), 20),
        ("burning-malediction", 2, ("malediction-1", "costs-fatigue-2"), 19),
        ("damage-resistance", 3, ("costs-fatigue-2",), 14),
        ("mind-reading", 1, ("telepathic",), 27),
        ("detect", 1, ("vague",), 3),
        ("detect", 1, ("precise",), 10),
    ],
)
def test_modified_cost_is_bound_to_executable_options(
    kind: str, level: int, modifiers: tuple[str, ...], expected: int
) -> None:
    assert (
        validate_binding(spec(kind, modifiers), level, TraitOptions(modifiers=modifiers))
        == expected
    )


@pytest.mark.parametrize(
    "kind,modifiers",
    [
        ("burning-malediction", ()),
        ("damage-resistance", ()),
        ("mind-reading", ("cybernetic",)),
        ("detect", ("vague", "precise")),
        ("detect", ("costs-fatigue-1", "costs-fatigue-2")),
    ],
)
def test_unsupported_combinations_never_only_charge_points(
    kind: str, modifiers: tuple[str, ...]
) -> None:
    with pytest.raises(ValidationError):
        validate_binding(spec(kind, modifiers), 1, TraitOptions(modifiers=modifiers))


def test_malediction_resistance_damage_and_rule16() -> None:
    ability = spec("burning-malediction", ("malediction-1",))
    state, _, result = resolve(ability, context(ability), [3, 3, 3, 4, 4, 4, 4])
    assert result == "hit"
    assert next(p.current for p in state.pools if p.id == "hp:b") == 6
    assert not effects(state)
    # Will 30 is capped at 16; equal 9 rolls vs defender 16 tie and resist.
    state, _, result = resolve(ability, replace(context(ability), will=30, target_will=16), [3] * 6)
    assert result == "resisted" and state.pools[1].current == 10


@pytest.mark.parametrize(
    "modifiers,dice,known",
    [
        (("vague",), [3, 3, 3], {"presence"}),
        (("vague",), [1, 1, 1], {"presence", "direction"}),
        (("precise",), [3, 3, 3], {"presence", "direction", "distance"}),
        ((), [6, 6, 6], set()),
    ],
)
def test_detect_reveals_only_authorized_granularity(
    modifiers: tuple[str, ...], dice: list[int], known: set[str]
) -> None:
    ability = spec("detect", modifiers)
    _, updated, _ = resolve(ability, context(ability), dice)
    assert {f.id for f in updated.perspective("a").facts} == known | {"visible"}
    assert {f.id for f in updated.perspective("b").facts} == {"secret"}


def test_reading_reads_surface_thoughts_not_target_knowledge() -> None:
    ability = spec("mind-reading", ("telepathic",))
    state, updated, result = resolve(ability, context(ability), [3, 3, 3, 4, 4, 4])
    assert result == "active" and effects(state)[0].expires_at is None
    assert {f.id for f in updated.perspective("a").facts} == {"visible", "thought"}
    state, _, cancelled = apply_ability(
        state,
        updated,
        command(2, "cancel"),
        ability,
        context(ability),
        rng=RecordedDice([]),
        system=True,
    )
    assert cancelled.outcome == "cancelled" and not effects(state)


def test_mindshield_adds_to_telepathic_resistance() -> None:
    ability = spec("mind-reading", ("telepathic",))
    _, updated, result = resolve(ability, replace(context(ability), mind_shield=5), [3] * 6)
    assert result == "resisted" and {f.id for f in updated.perspective("a").facts} == {"visible"}


def test_concentration_is_persistent_interruptible_and_not_free_instant_attack() -> None:
    ability = spec("mind-reading")
    started, state_world, _ = apply_ability(
        resources(),
        world(),
        command(),
        ability,
        context(ability),
        rng=RecordedDice([]),
        system=True,
    )
    with pytest.raises(ConflictError, match="not complete"):
        apply_ability(
            started,
            state_world,
            command(1, "resolve"),
            ability,
            context(ability),
            rng=RecordedDice([]),
            system=True,
        )
    hurt = ResourceState.model_validate_json(started.model_dump_json()).model_copy(
        update={
            "game_time": 1,
            "pools": tuple(
                p.model_copy(update={"current": 9}) if p.id == "hp:a" else p for p in started.pools
            ),
        }
    )
    updated, _, result = apply_ability(
        hurt,
        state_world,
        command(1, "resolve"),
        ability,
        context(ability),
        rng=RecordedDice([4, 4, 4]),
        system=True,
    )  # Will14-3 =11; 12 loses concentration.
    assert result.outcome == "interrupted" and not effects(updated)


@pytest.mark.parametrize(
    "override", ["actor_id", "location_id", "digital_mind", "psionic_blocked", "shared_language"]
)
def test_bad_targets_and_psi_suppression_reject_before_dice(override: str) -> None:
    ability = spec("mind-reading", ("telepathic",))
    ctx = context(ability)
    assert ctx.channel
    overrides: dict[str, str | bool] = {
        "actor_id": "b",
        "location_id": "elsewhere",
        "digital_mind": True,
        "psionic_blocked": True,
        "shared_language": False,
    }
    value = overrides[override]
    with pytest.raises(ValidationError):
        apply_ability(
            resources(),
            world(),
            command(),
            ability,
            replace(ctx, channel=ctx.channel.model_copy(update={override: value})),
            rng=RecordedDice([]),
            system=True,
        )


def test_missing_profile_authority_and_client_formula_fail_closed() -> None:
    ability = spec()
    for ctx, system in [
        (context(ability), False),
        (replace(context(ability), profile_id="gurps-lite-4e-2004"), True),
    ]:
        with pytest.raises(ValidationError):
            apply_ability(
                resources(), world(), command(), ability, ctx, rng=RecordedDice([]), system=system
            )
    with pytest.raises(SchemaError):
        AbilityCommand.model_validate({**command().model_dump(), "damage": 99})
    assert damage_resistance(resources(), "a") == 0


def test_shock_penalizes_iq_not_will_and_build_change_invalidates_concentration() -> None:
    mental = spec("mind-reading")
    ctx = replace(context(mental), shock=4)
    _, _, outcome = resolve(mental, ctx, [4, 4, 4, 4, 4, 4])
    assert outcome == "resisted"  # IQ14-4 fails on12.
    attack = spec("burning-malediction", ("malediction-1",))
    _, _, outcome = resolve(attack, replace(context(attack), shock=4), [4, 4, 4, 4, 4, 4, 1])
    assert outcome == "hit"  # Will14 unaffected byshock succeeds on12.
    started, updated_world, _ = apply_ability(
        resources(),
        world(),
        command(),
        mental,
        replace(context(mental), build_revision="first"),
        rng=RecordedDice([]),
        system=True,
    )
    with pytest.raises(ConflictError, match="build changed"):
        apply_ability(
            started.model_copy(update={"game_time": 1}),
            updated_world,
            command(1, "resolve"),
            mental,
            replace(context(mental), build_revision="changed"),
            rng=RecordedDice([]),
            system=True,
        )


def test_detect_analysis_takes_separate_concentration_and_does_not_reveal_early() -> None:
    ability = spec("detect")
    state, updated_world, _ = resolve(ability, context(ability), [3, 3, 3])
    state, updated_world, begun = apply_ability(
        state,
        updated_world,
        command(2, "analyze"),
        ability,
        context(ability),
        rng=RecordedDice([]),
        system=True,
    )
    assert begun.outcome == "concentrating" and not begun.revealed_fact_ids
    with pytest.raises(ConflictError):
        apply_ability(
            state,
            updated_world,
            command(3, "analyze"),
            ability,
            context(ability),
            rng=RecordedDice([]),
            system=True,
        )
    state, updated_world, finished = apply_ability(
        state.model_copy(update={"game_time": 2}),
        updated_world,
        command(3, "resolve"),
        ability,
        context(ability),
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert finished.outcome == "analyzed" and finished.revealed_fact_ids == ("analysis",)
    assert not effects(state)
