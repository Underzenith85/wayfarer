"""Independent animal and swarm combat cases from Campaigns B460-B461."""

from typing import Literal

import pytest
from pydantic import ValidationError as SchemaError
from test_creatures import catalog

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.creatures import representative_swarms
from wayfarer.engine.rules.types.creature import (
    LearnedCommand,
    Swarm,
    SwarmCell,
    SwarmOccupant,
)
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import InjuryTolerance
from wayfarer.engine.simulation.creature_combat import (
    DamageSwarm,
    NaturalAttackOutcome,
    ResolveNaturalAttack,
    ResolveSwarmTurn,
    SetSwarmArea,
    SwarmDamageOutcome,
    SwarmTurnOutcome,
    apply_creature_combat,
    propose_creature_actions,
    replay_creature_combat,
)
from wayfarer.engine.simulation.movement.creature_mounts import mount_transport
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"


def creature_state() -> ResourceState:
    entries = catalog()
    wolf = entries.compile("wolf", "Wolf", "creature:timber-wolf").creature
    dog = entries.compile("dog", "Dog", "creature:large-guard-dog").creature
    return ResourceState(
        creatures=(wolf, dog),
        pools=(
            Pool(id="hp:wolf", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="hp:dog", current=9, maximum=9, injury=InjuryStatus(profile_id=PROFILE)),
        ),
    )


def natural(**changes: object) -> ResolveNaturalAttack:
    values: dict[str, object] = {
        "id": "bite",
        "actor_id": "wolf",
        "expected_revision": 0,
        "target_id": "dog",
        "attack_id": "bite",
        "motivation": "predatory",
        "attacker_position": SwarmCell(q=0, r=0),
        "target_position": SwarmCell(q=0, r=0),
        "defense": "none",
        "target_ht": 12,
        "target_dr": 1,
    }
    values.update(changes)
    return ResolveNaturalAttack.model_validate(values)


def test_natural_attack_uses_compiled_damage_reach_dr_and_common_injury() -> None:
    seed = creature_state()
    dice = RecordedDice([2, 2, 2, 4])
    updated, result = apply_creature_combat(seed, natural(), rng=dice, system=True)
    assert isinstance(result, NaturalAttackOutcome) and result.hit
    assert result.damage_dice == (4,) and result.basic_damage == 2
    assert result.injury is not None
    assert (result.injury.effective_resistance, result.injury.injury) == (1, 1)
    assert next(pool.current for pool in updated.pools if pool.id == "hp:dog") == 8
    assert dice.exhausted()

    restored = ResourceState.model_validate_json(updated.model_dump_json())
    retried, replayed = apply_creature_combat(
        restored, natural(), rng=RecordedDice([]), system=True
    )
    assert (retried, replayed) == (restored, result)
    with pytest.raises(ValidationError, match="reach"):
        apply_creature_combat(
            seed,
            natural(target_position=SwarmCell(q=1, r=0)),
            rng=RecordedDice([]),
            system=True,
        )


def test_natural_attack_cannot_bypass_persisted_injury_tolerance() -> None:
    seed = creature_state()
    pools = tuple(
        pool.model_copy(
            update={
                "injury": pool.injury.model_copy(
                    update={
                        "anatomy": "creature",
                        "tolerance": InjuryTolerance(
                            structure="diffuse", no_brain=True, no_vitals=True
                        ),
                    }
                )
            }
        )
        if pool.id == "hp:dog" and pool.injury is not None
        else pool
        for pool in seed.pools
    )
    updated, result = apply_creature_combat(
        seed.model_copy(update={"pools": pools}),
        natural(target_dr=0),
        rng=RecordedDice([2, 2, 2, 6]),
        system=True,
    )
    assert isinstance(result, NaturalAttackOutcome) and result.injury is not None
    # 1d-2 cutting would inflict 6 injury before Diffuse caps this attack at 2.
    assert (result.basic_damage, result.injury.injury) == (4, 2)
    assert next(pool.current for pool in updated.pools if pool.id == "hp:dog") == 7


def test_creature_proposals_fail_closed_for_training_anatomy_and_condition() -> None:
    state = creature_state()
    dog = next(entry for entry in state.creatures if entry.actor_id == "dog")
    assert not any(
        proposal.attack_id for proposal in propose_creature_actions(state, "dog", "commanded")
    )
    trained = dog.model_copy(
        update={
            "last_command_id": "guard",
            "learned_commands": (
                LearnedCommand(
                    id="guard",
                    required_training_level=4,
                    competence=13,
                    learned_from_handler_id="handler",
                ),
            ),
        }
    )
    state = state.model_copy(
        update={
            "creatures": tuple(
                trained if entry.actor_id == "dog" else entry for entry in state.creatures
            )
        }
    )
    proposals = propose_creature_actions(state, "dog", "commanded")
    attacks = tuple(value for value in proposals if value.attack_id)
    assert attacks and all("parry" not in value.defenses for value in attacks)
    assert {value.maneuver for value in proposals}.isdisjoint({"aim", "feint", "ready"})

    pools = tuple(
        pool.model_copy(update={"injury": pool.injury.model_copy(update={"unconscious": True})})
        if pool.id == "hp:dog" and pool.injury is not None
        else pool
        for pool in state.pools
    )
    unconscious = state.model_copy(update={"pools": pools})
    assert [
        value.maneuver for value in propose_creature_actions(unconscious, "dog", "defensive")
    ] == ["do-nothing"]


def test_mounted_creature_attack_uses_existing_transport_separation_state() -> None:
    horse = catalog().compile("horse", "Horse", "creature:cavalry-horse").creature
    dog = catalog().compile("dog", "Dog", "creature:large-guard-dog").creature
    transport = mount_transport(horse, transport_id="ride", rider_id="rider")
    state = ResourceState(
        creatures=(horse, dog),
        transports=(transport,),
        pools=(
            Pool(id="hp:horse", current=22, maximum=22, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="hp:dog", current=9, maximum=9, injury=InjuryStatus(profile_id=PROFILE)),
        ),
    )
    assert any(
        value.attack_id == "kick"
        for value in propose_creature_actions(
            state, "horse", "commanded", mounted_transport_id="ride"
        )
    )
    separated = transport.model_copy(update={"status": "rider-separated", "occupants": ()})
    with pytest.raises(ValidationError, match="#396/#397"):
        propose_creature_actions(
            state.model_copy(update={"transports": (separated,)}),
            "horse",
            "commanded",
            mounted_transport_id="ride",
        )


def swarm_state(kind: str = "bees", *, remaining: int | None = None) -> ResourceState:
    spec = next(entry for entry in representative_swarms() if entry.kind == kind)
    hp = spec.dispersal_hp if remaining is None else remaining
    swarm = Swarm(
        id="cloud",
        actor_id="swarm-actor",
        spec=spec,
        area=(SwarmCell(q=0, r=0),),
        occupants=(
            SwarmOccupant(
                actor_id="a",
                position=SwarmCell(q=0, r=0),
                entered_at=0,
                protection="ordinary-clothing" if kind == "bees" else "none",
                armor_dr=2,
                ht=10,
            ),
        ),
        remaining_hp=hp,
        next_attack_at=0,
    )
    return ResourceState(
        swarms=(swarm,),
        pools=(
            Pool(
                id="hp:swarm-actor",
                current=hp,
                maximum=spec.dispersal_hp,
                injury=InjuryStatus(profile_id=PROFILE, anatomy="swarm"),
            ),
            Pool(id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="hp:b", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
        ),
    )


def test_swarm_only_affects_authoritative_occupants_on_its_cadence() -> None:
    seed = swarm_state()
    first = ResolveSwarmTurn(
        id="turn-0", actor_id="swarm-actor", expected_revision=0, swarm_id="cloud"
    )
    protected, outcome = apply_creature_combat(seed, first, rng=RecordedDice([]), system=True)
    assert isinstance(outcome, SwarmTurnOutcome)
    assert outcome.targets[0].protected
    assert next(pool.current for pool in protected.pools if pool.id == "hp:a") == 10
    assert next(pool.current for pool in protected.pools if pool.id == "hp:b") == 10
    with pytest.raises(ConflictError, match="cadence"):
        apply_creature_combat(
            protected,
            ResolveSwarmTurn(
                id="too-soon",
                actor_id="swarm-actor",
                expected_revision=1,
                swarm_id="cloud",
            ),
            rng=RecordedDice([]),
            system=True,
        )
    elapsed = protected.model_copy(update={"game_time": 2})
    attacked, outcome = apply_creature_combat(
        elapsed,
        ResolveSwarmTurn(
            id="turn-2", actor_id="swarm-actor", expected_revision=1, swarm_id="cloud"
        ),
        rng=RecordedDice([]),
        system=True,
    )
    assert isinstance(outcome, SwarmTurnOutcome) and not outcome.targets[0].protected
    assert next(pool.current for pool in attacked.pools if pool.id == "hp:a") == 9
    assert next(pool.current for pool in attacked.pools if pool.id == "hp:b") == 10


def test_swarm_diffuse_damage_and_dispersal_are_exactly_once() -> None:
    seed = swarm_state("rats", remaining=2)
    command = DamageSwarm(
        id="shield",
        actor_id="a",
        expected_revision=0,
        swarm_id="cloud",
        countermeasure="shield",
    )
    dispersed, result = apply_creature_combat(
        seed, command, rng=RecordedDice([1, 1, 1]), system=True
    )
    assert isinstance(result, SwarmDamageOutcome)
    assert result.dispersed and result.remaining_hp == 0
    assert not dispersed.swarms[0].active and dispersed.swarms[0].occupants == ()
    restored = ResourceState.model_validate_json(dispersed.model_dump_json())
    assert apply_creature_combat(restored, command, rng=RecordedDice([]), system=True) == (
        restored,
        result,
    )
    with pytest.raises(ConflictError, match="already dispersed"):
        apply_creature_combat(
            dispersed,
            command.model_copy(update={"id": "again", "expected_revision": 1}),
            rng=RecordedDice([]),
            system=True,
        )


def test_swarm_area_and_countermeasure_schemas_fail_closed() -> None:
    seed = swarm_state("bats")
    ignored, result = apply_creature_combat(
        seed,
        DamageSwarm(
            id="stomp",
            actor_id="a",
            expected_revision=0,
            swarm_id="cloud",
            countermeasure="stomp",
        ),
        rng=RecordedDice([]),
        system=True,
    )
    assert isinstance(result, SwarmDamageOutcome) and result.ignored
    assert ignored.swarms[0].remaining_hp == 8
    with pytest.raises(SchemaError, match="authoritative area"):
        Swarm.model_validate(
            {
                **seed.swarms[0].model_dump(),
                "occupants": (
                    {
                        "actor_id": "a",
                        "position": {"q": 1, "r": 0},
                        "entered_at": 0,
                        "ht": 10,
                    },
                ),
            }
        )
    with pytest.raises(ValidationError, match="connected"):
        apply_creature_combat(
            seed,
            SetSwarmArea(
                id="split",
                actor_id="swarm-actor",
                expected_revision=0,
                swarm_id="cloud",
                area=(SwarmCell(q=0, r=0), SwarmCell(q=2, r=0)),
            ),
            rng=RecordedDice([]),
            system=True,
        )


def test_creature_combat_replays_from_json_without_new_randomness() -> None:
    seed = creature_state()
    command = natural()
    live = replay_creature_combat(seed, (command,), rng=RecordedDice([2, 2, 2, 4]))
    restored = ResourceState.model_validate_json(live.model_dump_json())
    assert replay_creature_combat(restored, (command,), rng=RecordedDice([])) == restored


def test_special_monster_attack_rejects_without_a_trait_procedure() -> None:
    basilisk = catalog().compile("basilisk", "Basilisk", "creature:basilisk").creature
    dog = catalog().compile("dog", "Dog", "creature:large-guard-dog").creature
    state = ResourceState(
        creatures=(basilisk, dog),
        pools=(
            Pool(id="hp:basilisk", current=2, maximum=2, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="hp:dog", current=9, maximum=9, injury=InjuryStatus(profile_id=PROFILE)),
        ),
    )
    with pytest.raises(ValidationError, match="trait procedure"):
        apply_creature_combat(
            state,
            natural(
                id="gaze",
                actor_id="basilisk",
                attack_id="death-gaze",
                motivation="predatory",
                target_dr=0,
            ),
            rng=RecordedDice([2, 2, 2]),
            system=True,
        )
