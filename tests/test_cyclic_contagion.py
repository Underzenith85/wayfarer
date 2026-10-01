"""Contagious Cyclic B103-104 using the B442-443 authored Illness contact oracle."""

from dataclasses import replace

import pytest
from test_attack_defense_traits import approved, channel, command, resources, world
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import EnhancementParameters, ModifierSelection
from wayfarer.engine.rules.types.disease import ContactExposure
from wayfarer.engine.simulation.health.cyclic import StopCyclic, stop
from wayfarer.engine.simulation.health.cyclic_contagion import ExposeCyclic, expose
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.attack_defense import apply_trait_attack
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError


def context() -> tuple[ResourceState, World]:
    w = world()
    location = next(e.location_id for e in w.entities if e.id == "b")
    w = replace(
        w,
        entities=w.entities
        + (
            Entity("c", EntityKind.ACTOR, "Secondary", location_id=location),
            Entity("d", EntityKind.ACTOR, "Another", location_id=location),
        ),
    )
    r = resources()
    hp = next(p for p in r.pools if p.id == "hp:b")
    r = r.model_copy(
        update={
            "pools": r.pools
            + (hp.model_copy(update={"id": "hp:c"}), hp.model_copy(update={"id": "hp:d"}))
        }
    )
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "tox"}).model_copy(
                update={
                    "attack_modifiers": (
                        ModifierSelection(
                            definition_id="modifier:enhancement:cyclic",
                            option="disease",
                            parameters=EnhancementParameters(
                                interval_seconds=86400,
                                cycles=3,
                                stop_condition="treatment",
                                contagious="mild",
                                damage_kind="toxic",
                            ),
                        ),
                    )
                }
            ),
        )
    )
    target, _ = approved()
    r, _ = apply_trait_attack(
        r,
        w,
        command(),
        attacker,
        target,
        compiler.definitions,
        (channel(damage_type="tox", basic_damage=2, contagion_vector="respiratory"),),
        target_ht=12,
        rng=RecordedDice([3] * 60),
        authorized_actor_id="a",
        system=True,
    )
    return r, w


def contact(
    state: ResourceState,
    target: str = "c",
    *,
    identifier: str = "contact",
    kind: str = "close-conversation",
) -> ContactExposure:
    source = state.cyclic_attacks[0]
    return ContactExposure.model_validate(
        {
            "id": identifier,
            "actor_id": target,
            "disease_id": source.attack_id,
            "vector": "respiratory",
            "contact": kind,
            "occurred_at": state.game_time,
            "carrier_id": "b",
        }
    )


def exposure(
    state: ResourceState, w: World, relationship: ContactExposure, *, identifier: str = "exposure"
) -> tuple[ResourceState, ExposeCyclic]:
    cmd = ExposeCyclic(
        id=identifier,
        actor_id=relationship.actor_id,
        expected_revision=state.revision,
        source_attack_id=state.cyclic_attacks[0].id,
        relationship_id=relationship.id,
    )
    return expose(state, w, cmd, relationship, ht=10, system=True), cmd


def hp(state: ResourceState, subject: str) -> int:
    return next(p.current for p in state.pools if p.id == "hp:" + subject)


def clock(state: ResourceState, to: int, dice: list[int]) -> ResourceState:
    from test_resources import engine

    from wayfarer.engine.simulation.resources import Advance

    reducer = engine().for_world(context()[1])
    return reducer.apply(
        state,
        Advance(
            id="contagion-clock:" + str(to), actor_id="a", expected_revision=state.revision, to=to
        ),
        system=True,
        rng=RecordedDice(dice),
    )


def test_exposure_checks_end_of_day_then_independent_incubation_and_cycles() -> None:
    state, w = context()
    state, _ = exposure(state, w, contact(state))
    state = clock(state, 86399, [])
    assert hp(state, "c") == 10 and len(state.cyclic_attacks) == 1
    state = clock(state, 86400, [6, 6, 6, 2] + [3] * 60)
    assert state.cyclic_exposures[0].stage == "infected" and hp(state, "c") == 10
    infection = next(a for a in state.cyclic_attacks if a.actor_id == "c")
    assert infection.cycle == 0 and infection.remaining == 3 and infection.due == 172800
    state = clock(state, 172800, [2] * 100)
    assert hp(state, "c") == 8 and hp(state, "b") == 4
    state = clock(state, 345600, [2] * 100)
    assert (
        hp(state, "c") == 4
        and not next(a for a in state.cyclic_attacks if a.actor_id == "c").active
    )


def test_source_infection_can_stop_after_contact_without_erasing_secondary_exposure() -> None:
    state, w = context()
    state, _ = exposure(state, w, contact(state))
    source = state.cyclic_attacks[0]
    state = stop(
        state,
        StopCyclic(
            id="cure-carrier",
            actor_id="b",
            expected_revision=state.revision,
            attack_id=source.id,
            condition="treatment",
        ),
        system=True,
    )
    state = clock(state, 86400, [6, 6, 6])
    assert not next(a for a in state.cyclic_attacks if a.actor_id == "b").active
    assert next(a for a in state.cyclic_attacks if a.actor_id == "c").active


def test_least_advantageous_daily_contact_roll_is_shared_and_never_duplicates_infection() -> None:
    state, w = context()
    state, _ = exposure(state, w, contact(state, identifier="speaking", kind="close-conversation"))
    state, _ = exposure(
        state,
        w,
        contact(state, identifier="intimate", kind="intimate-contact"),
        identifier="second",
    )
    state = clock(state, 86400, [3, 3, 3, 2] + [3] * 60)
    assert all(e.check and e.check.effective_target == 7 for e in state.cyclic_exposures)
    assert all(e.stage == "infected" for e in state.cyclic_exposures)
    assert len([a for a in state.cyclic_attacks if a.actor_id == "c"]) == 1


def test_independent_targets_have_independent_resistance_and_stop_state() -> None:
    state, w = context()
    state, _ = exposure(state, w, contact(state, "c", identifier="first"))
    state, _ = exposure(state, w, contact(state, "d", identifier="second"), identifier="second")
    state = clock(state, 86400, [6, 6, 6, 1, 1, 1, 2] + [2] * 60)
    stages = {e.relationship.actor_id: e.stage for e in state.cyclic_exposures}
    assert set(stages.values()) == {"infected", "resisted"}
    assert len([a for a in state.cyclic_attacks if a.actor_id in {"c", "d"}]) == 1


def test_authority_staleness_retry_relationship_and_world_guards() -> None:
    state, w = context()
    relation = contact(state)
    updated, cmd = exposure(state, w, relation)
    assert expose(updated, w, cmd, relation, ht=10, system=True) == updated
    with pytest.raises(ValidationError):
        expose(state, w, cmd, relation, ht=10)
    with pytest.raises(ConflictError):
        expose(
            state, w, cmd.model_copy(update={"expected_revision": 0}), relation, ht=10, system=True
        )
    with pytest.raises(ConflictError):
        expose(
            updated,
            w,
            cmd.model_copy(update={"relationship_id": "different"}),
            relation,
            ht=10,
            system=True,
        )
    with pytest.raises(ValidationError):
        expose(state, w, cmd, relation.model_copy(update={"carrier_id": "a"}), ht=10, system=True)
    changed = replace(
        w,
        entities=tuple(
            replace(e, location_id="elsewhere") if e.id == "c" else e for e in w.entities
        ),
    )
    with pytest.raises(ValidationError):
        expose(state, changed, cmd, relation, ht=10, system=True)
    cured = stop(
        state,
        StopCyclic(
            id="cure",
            actor_id="b",
            expected_revision=state.revision,
            attack_id=state.cyclic_attacks[0].id,
            condition="treatment",
        ),
        system=True,
    )
    with pytest.raises(ValidationError):
        expose(
            cured,
            w,
            cmd.model_copy(update={"expected_revision": cured.revision}),
            relation,
            ht=10,
            system=True,
        )


def test_secondary_subject_transmits_its_own_independent_infection() -> None:
    state, w = context()
    state, _ = exposure(state, w, contact(state))
    state = clock(state, 86400, [6, 6, 6, 2] + [3] * 60)
    secondary = next(a for a in state.cyclic_attacks if a.actor_id == "c")
    relation = ContactExposure(
        id="secondary-contact",
        actor_id="d",
        disease_id=secondary.attack_id,
        vector="respiratory",
        contact="close-conversation",
        occurred_at=state.game_time,
        carrier_id="c",
    )
    cmd = ExposeCyclic(
        id="secondary-exposure",
        actor_id="d",
        expected_revision=state.revision,
        source_attack_id=secondary.id,
        relationship_id=relation.id,
    )
    state = expose(state, w, cmd, relation, ht=10, system=True)
    state = clock(state, 172800, [6, 6, 6, 2, 2] + [3] * 60)
    tertiary = next(a for a in state.cyclic_attacks if a.actor_id == "d")
    assert tertiary.cycle == 0 and tertiary.remaining == 3
    state = stop(
        state,
        StopCyclic(
            id="stop-secondary",
            actor_id="c",
            expected_revision=state.revision,
            attack_id=secondary.id,
            condition="treatment",
        ),
        system=True,
    )
    assert next(a for a in state.cyclic_attacks if a.actor_id == "d").active
    assert not next(a for a in state.cyclic_attacks if a.actor_id == "c").active


def test_natural_immunity_is_retained_without_a_new_secret_roll() -> None:
    state, w = context()
    state, _ = exposure(state, w, contact(state))
    state = clock(state, 86400, [1, 1, 1, 2])
    assert state.cyclic_exposures[0].immune and state.cyclic_exposures[0].stage == "resisted"
    state, _ = exposure(
        state, w, contact(state, identifier="next-day"), identifier="next-day-command"
    )
    # Only the carrier's next damage die is needed; immunity consumes no dice.
    state = clock(state, 172800, [2])
    assert all(e.immune for e in state.cyclic_exposures)
    assert not any(a.actor_id == "c" for a in state.cyclic_attacks)


def test_persisted_clock_retry_consumes_no_new_infection_roll() -> None:
    from test_resources import engine

    from wayfarer.engine.simulation.resources import Advance

    state, w = context()
    state, _ = exposure(state, w, contact(state))
    cmd = Advance(id="clock-repeat", actor_id="a", expected_revision=state.revision, to=86400)
    reducer = engine().for_world(w)
    result = reducer.apply(state, cmd, system=True, rng=RecordedDice([6, 6, 6, 2] + [3] * 60))
    restarted = ResourceState.model_validate_json(result.model_dump_json())
    assert reducer.apply(restarted, cmd, system=True, rng=RecordedDice([])) == restarted
    assert len(restarted.cyclic_attacks) == 2
