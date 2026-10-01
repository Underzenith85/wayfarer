"""Independent physiology expectations, Characters 4e B41-160."""

import pytest
from trait_support import approved_build, options, trait_compiler

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.rules.supernatural import inventory
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.physiology import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.traits.physiology import package as physiology_package
from wayfarer.engine.rules.types.injury import ElectricalStun, InjuryStatus
from wayfarer.engine.rules.types.location import LastingInjury
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.medical.commands import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
)
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits.physiology import (
    PhysiologyCommand,
    PhysiologyInterval,
    apply_physiology_interval,
    history,
)
from wayfarer.errors import ConflictError, ValidationError


class FixedDice:
    def randbelow(self, upper: int) -> int:
        return min(2, upper - 1)


def compiler() -> CharacterCompiler:
    return trait_compiler("physiology", PROFILE, physiology_package(), hooks=RUNTIME_HOOKS)


def approved(*purchases: Purchase) -> tuple[ValidatedBuild, CharacterCompiler]:
    return approved_build(compiler(), *purchases)


def test_registry_and_inventory_account_for_all_37_entries() -> None:
    assert len(BINDINGS) == 37 and len({binding.id for binding in BINDINGS}) == 37
    rows = {
        row.id: row for row in inventory().entries if row.id in {binding.id for binding in BINDINGS}
    }
    assert set(rows) == {binding.id for binding in BINDINGS}
    assert all(
        row.blockers == () and row.evidence == ("tests/test_physiology_traits.py",)
        for row in rows.values()
    )


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [
        (Purchase(definition_id="advantage:doesnt-breathe"), 20),
        (Purchase(definition_id="advantage:radiation-tolerance", trait=options(divisor=100)), 30),
        (Purchase(definition_id="advantage:regeneration", trait=options(rate="fast")), 50),
        (Purchase(definition_id="disadvantage:bestial", trait=options(speech=False)), -15),
        (
            Purchase(
                definition_id="disadvantage:dependency",
                trait=options(rarity="rare", interval="day"),
            ),
            -90,
        ),
        (Purchase(definition_id="disadvantage:unhealing", trait=options(kind="total")), -30),
        (
            Purchase(
                definition_id="disadvantage:weakness",
                trait=options(rarity="common", interval="minute"),
            ),
            -45,
        ),
    ],
)
def test_fixed_and_variable_costs_are_compiler_owned(purchase: Purchase, expected: int) -> None:
    build, _ = approved(purchase)
    assert (
        next(
            entry.cost for entry in build.purchases if entry.definition_id == purchase.definition_id
        )
        == expected
    )


def test_projection_supplies_survival_environment_and_recovery_rules() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:doesnt-breathe"),
        Purchase(definition_id="advantage:doesnt-eat-or-drink"),
        Purchase(definition_id="advantage:breath-holding", amount=2),
        Purchase(definition_id="advantage:extended-lifespan", amount=3),
        Purchase(definition_id="advantage:radiation-tolerance", trait=options(divisor=20)),
        Purchase(definition_id="advantage:regeneration", trait=options(rate="fast")),
        Purchase(definition_id="advantage:pressure-support", amount=2),
        Purchase(definition_id="advantage:vacuum-support"),
    )
    traits = physiology_traits(build, engine.definitions)
    assert traits.breath_multiplier() is None
    assert traits.survival_requirements() == frozenset({"sleep"})
    assert traits.lifespan_multiplier() == 8 and traits.radiation_divisor() == 20
    assert traits.regeneration_interval() == 60
    assert traits.environmental_protection("pressure") == 20
    assert traits.environmental_protection("vacuum") == 10


def state(current: int = 5, time: int = 60) -> ResourceState:
    return ResourceState(
        game_time=time,
        pools=(
            Pool(id="hp:a", current=current, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
        ),
    )


def command(revision: int = 0, identifier: str = "regen") -> PhysiologyCommand:
    return PhysiologyCommand(
        id="physiology-" + identifier,
        actor_id="a",
        expected_revision=revision,
        interval_id=identifier,
    )


def test_regeneration_updates_hp_atomically_and_retries_exactly_once_after_restart() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:regeneration", trait=options(rate="fast"))
    )
    interval = PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=60, amount=2)
    updated, outcome = apply_physiology_interval(
        state(),
        command(),
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert (outcome.hp_before, outcome.hp_after, outcome.kind) == (5, 6, "regenerated")
    assert updated.pools[0].current == 6
    restarted = ResourceState.model_validate_json(updated.model_dump_json())
    assert history(restarted)[0].outcome == outcome
    assert apply_physiology_interval(
        restarted,
        command(),
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    ) == (restarted, outcome)


def test_weakness_and_extra_life_use_the_same_hp_ledger_and_limits() -> None:
    weak, weak_engine = approved(
        Purchase(
            definition_id="disadvantage:weakness", trait=options(rarity="common", interval="minute")
        )
    )
    injured, result = apply_physiology_interval(
        state(),
        command(identifier="weak"),
        PhysiologyInterval(id="weak", actor_id="a", kind="weakness", due=60, amount=3),
        weak,
        weak_engine.definitions,
        rng=FixedDice(),
        authorized_actor_id="a",
        system=True,
    )
    assert result.kind == "injured" and injured.pools[0].current == 2
    life, life_engine = approved(Purchase(definition_id="advantage:extra-life"))
    revived, result = apply_physiology_interval(
        extra_life_state(-10),
        command(identifier="life"),
        PhysiologyInterval(id="life", actor_id="a", kind="extra-life", due=60),
        life,
        life_engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert result.kind == "revived" and revived.pools[0].current == 10


def test_interval_rejects_early_unauthorized_and_stale_commands() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:regeneration", trait=options(rate="regular"))
    )
    interval = PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=60)
    with pytest.raises(ValidationError, match="authority"):
        apply_physiology_interval(
            state(),
            command(),
            interval,
            build,
            engine.definitions,
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError):
        apply_physiology_interval(
            state().model_copy(update={"revision": 1}),
            command(),
            interval,
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError, match="not due"):
        apply_physiology_interval(
            state(time=59),
            command(),
            interval,
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )


@pytest.mark.parametrize(
    ("rate", "period", "maximum", "expected"),
    [
        ("slow", 43200, 10, 1),
        ("regular", 3600, 10, 1),
        ("fast", 60, 10, 1),
        ("very-fast", 1, 10, 1),
        ("extreme", 1, 10, 10),
        ("fast", 60, 30, 3),
    ],
)
def test_regeneration_uses_b80_rate_and_b424_hp_scaling(
    rate: str, period: int, maximum: int, expected: int
) -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:regeneration", trait=options(rate=rate))
    )
    resources = state(current=0, time=period).model_copy(
        update={
            "pools": (
                Pool(
                    id="hp:a", current=0, maximum=maximum, injury=InjuryStatus(profile_id=PROFILE)
                ),
            )
        }
    )
    interval = PhysiologyInterval(
        id="regen", actor_id="a", kind="regeneration", due=period, amount=999
    )
    updated, outcome = apply_physiology_interval(
        resources,
        command(),
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert updated.pools[0].current == outcome.hp_after == expected
    assert updated.pools[0].injury == resources.pools[0].injury


def test_regeneration_consumes_due_tick_across_ids_and_serialized_restart() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:regeneration", trait=options(rate="fast"))
    )
    interval = PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=60)
    updated, _ = apply_physiology_interval(
        state(),
        command(),
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    updated = ResourceState.model_validate_json(updated.model_dump_json())
    for candidate in (interval, interval.model_copy(update={"id": "alias"})):
        retry = command(revision=1, identifier=candidate.id).model_copy(update={"id": "another"})
        with pytest.raises(ConflictError, match="consumed"):
            apply_physiology_interval(
                updated,
                retry,
                candidate,
                build,
                engine.definitions,
                authorized_actor_id="a",
                system=True,
            )
    assert updated.pools[0].current == 6
    next_interval = interval.model_copy(update={"id": "next", "due": 120})
    next_state, _ = apply_physiology_interval(
        updated.model_copy(update={"game_time": 120}),
        command(revision=1, identifier="next"),
        next_interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert next_state.pools[0].current == 7


def test_regeneration_rejects_wrong_rate_and_changed_retry_payload() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:regeneration", trait=options(rate="fast"))
    )
    for due in (0, 59, 61):
        interval = PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=due)
        with pytest.raises(ValidationError, match="approved rate"):
            apply_physiology_interval(
                state(time=120),
                command(),
                interval,
                build,
                engine.definitions,
                authorized_actor_id="a",
                system=True,
            )
    interval = PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=60)
    updated, _ = apply_physiology_interval(
        state(),
        command(),
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    with pytest.raises(ConflictError, match="already used"):
        apply_physiology_interval(
            updated,
            command(),
            interval.model_copy(update={"amount": 2}),
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )


def test_regeneration_clamps_to_maximum_and_refuses_dead_patients() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:regeneration", trait=options(rate="extreme"))
    )
    interval = PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=60)
    resources = state(current=9)
    updated, _ = apply_physiology_interval(
        resources,
        command(),
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert updated.pools[0].current == 10
    dead = resources.model_copy(
        update={
            "pools": (
                resources.pools[0].model_copy(
                    update={"injury": InjuryStatus(profile_id=PROFILE, dead=True)}
                ),
            )
        }
    )
    with pytest.raises(ValidationError, match="Dead"):
        apply_physiology_interval(
            dead,
            command(),
            interval,
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )


def extra_life_state(current: int = -10, *, dead: bool = True) -> ResourceState:
    return state(current).model_copy(
        update={
            "pools": (
                Pool(
                    id="hp:a",
                    current=current,
                    maximum=10,
                    injury=InjuryStatus(profile_id=PROFILE, dead=dead),
                ),
            )
        }
    )


def life_interval(identifier: str = "life") -> PhysiologyInterval:
    return PhysiologyInterval(id=identifier, actor_id="a", kind="extra-life", due=60)


def test_extra_life_restores_living_state_and_preserves_actor_anatomy() -> None:
    build, engine = approved(Purchase(definition_id="advantage:extra-life"))
    lasting = LastingInjury(
        id="old-scar",
        location="face",
        kind="scarred",
        duration="permanent",
        inflicted_at=0,
        injury=1,
    )
    injury = InjuryStatus(
        profile_id=PROFILE,
        anatomy="human",
        male_groin=True,
        dead=True,
        unconscious=True,
        mortal_wound=True,
        mortal_wound_due=120,
        mortal_wound_started=10,
        shock=4,
        shock_expires=2,
        stunned=True,
        prone=True,
        electrical_stun=ElectricalStun(
            source_id="wire",
            contact_ends_turn=0,
            recovery_starts_turn=1,
        ),
        lasting_injuries=(lasting,),
    )
    resources = extra_life_state().model_copy(
        update={
            "pools": (
                extra_life_state().pools[0].model_copy(update={"injury": injury}),
                Pool(id="hp:b", current=7, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            )
        }
    )
    updated, outcome = apply_physiology_interval(
        resources,
        command(identifier="life"),
        life_interval(),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    restored = updated.pools[0]
    assert outcome.kind == "revived" and restored.current == restored.maximum == 10
    assert restored.injury is not None and not restored.injury.incapacitated
    assert not restored.injury.dead and not restored.injury.unconscious
    assert not restored.injury.mortal_wound and restored.injury.mortal_wound_due is None
    assert restored.injury.mortal_wound_started == 0
    assert restored.injury.shock == restored.injury.shock_expires == 0
    assert not restored.injury.stunned and restored.injury.electrical_stun is None
    assert restored.injury.anatomy == "human" and restored.injury.male_groin
    assert restored.injury.lasting_injuries == (lasting,) and restored.injury.prone
    assert updated.pools[1] == resources.pools[1]
    assert resources.pools[0].injury == injury
    assert ResourceState.model_validate_json(updated.model_dump_json()) == updated


@pytest.mark.parametrize("current", [-50, -10, -1, 0, 5])
def test_extra_life_negative_hp_is_not_proof_of_death(current: int) -> None:
    build, engine = approved(Purchase(definition_id="advantage:extra-life"))
    resources = extra_life_state(current, dead=False)
    updated, outcome = apply_physiology_interval(
        resources,
        command(identifier="life"),
        life_interval(),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.kind == "unavailable"
    assert updated.pools == resources.pools
    assert not any(entry.outcome.kind == "revived" for entry in history(updated))


def test_extra_life_actual_death_can_revive_without_negative_hp_threshold() -> None:
    build, engine = approved(Purchase(definition_id="advantage:extra-life"))
    updated, outcome = apply_physiology_interval(
        extra_life_state(5),
        command(identifier="life"),
        life_interval(),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.kind == "revived" and updated.pools[0].current == 10
    assert updated.pools[0].injury is not None and not updated.pools[0].injury.dead


def test_extra_life_receipts_and_remaining_lives_survive_restart() -> None:
    build, engine = approved(Purchase(definition_id="advantage:extra-life"))
    request = command(identifier="life")
    interval = life_interval()
    updated, outcome = apply_physiology_interval(
        extra_life_state(),
        request,
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    restarted = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_physiology_interval(
        restarted,
        request,
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    ) == (restarted, outcome)
    with pytest.raises(ConflictError, match="already used"):
        apply_physiology_interval(
            restarted,
            request,
            interval.model_copy(update={"amount": 2}),
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )
    new_death = restarted.model_copy(update={"pools": extra_life_state().pools})
    exhausted, unavailable = apply_physiology_interval(
        new_death,
        command(revision=1, identifier="second"),
        life_interval("second"),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert unavailable.kind == "unavailable" and exhausted.pools == new_death.pools
    assert sum(entry.outcome.kind == "revived" for entry in history(exhausted)) == 1


def test_extra_life_interval_alias_cannot_spend_another_life() -> None:
    build, engine = approved(Purchase(definition_id="advantage:extra-life", amount=2))
    updated, _ = apply_physiology_interval(
        extra_life_state(),
        command(identifier="life"),
        life_interval(),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    new_death = updated.model_copy(update={"pools": extra_life_state().pools})
    alias = command(revision=1, identifier="life").model_copy(update={"id": "alias"})
    with pytest.raises(ConflictError, match="already consumed"):
        apply_physiology_interval(
            new_death,
            alias,
            life_interval(),
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )
    second, outcome = apply_physiology_interval(
        new_death,
        command(revision=1, identifier="second"),
        life_interval("second"),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.kind == "revived" and second.pools[0].injury is not None
    assert not second.pools[0].injury.dead
    assert sum(entry.outcome.kind == "revived" for entry in history(second)) == 2


@pytest.mark.parametrize(("authority", "system"), [("b", True), ("a", False)])
def test_extra_life_rejects_unauthorized_retries(authority: str, system: bool) -> None:
    build, engine = approved(Purchase(definition_id="advantage:extra-life"))
    updated, _ = apply_physiology_interval(
        extra_life_state(),
        command(identifier="life"),
        life_interval(),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    with pytest.raises(ValidationError, match="authority"):
        apply_physiology_interval(
            updated,
            command(identifier="life"),
            life_interval(),
            build,
            engine.definitions,
            authorized_actor_id=authority,
            system=system,
        )


def test_extra_life_rejects_stale_new_commands() -> None:
    build, engine = approved(Purchase(definition_id="advantage:extra-life"))
    resources = extra_life_state().model_copy(update={"revision": 1})
    with pytest.raises(ConflictError, match="revision changed"):
        apply_physiology_interval(
            resources,
            command(identifier="life"),
            life_interval(),
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )


@pytest.mark.parametrize("modifier", ["copy", "reincarnation", "requires-body"])
def test_extra_life_unimplemented_modifiers_do_not_claim_generic_revival(modifier: str) -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:extra-life", trait=TraitOptions(modifiers=(modifier,)))
    )
    with pytest.raises(ValidationError, match="separately supported"):
        apply_physiology_interval(
            extra_life_state(),
            command(identifier="life"),
            life_interval(),
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )


@pytest.mark.parametrize(
    ("frequency", "due", "cadence"),
    [("minute", 60, 60), ("hour", 4200, 600), ("day", 90000, 3600), ("week", 626400, 21600)],
)
def test_dependency_source_damage_cadence_and_consumption(
    frequency: str, due: int, cadence: int
) -> None:
    build, engine = approved(
        Purchase(
            definition_id="disadvantage:dependency",
            trait=options(rarity="common", interval=frequency),
        )
    )
    interval = PhysiologyInterval(id="dose", actor_id="a", kind="dependency", due=due, amount=999)
    request = command(identifier="dose")
    updated, outcome = apply_physiology_interval(
        state(time=due),
        request,
        interval,
        build,
        engine.definitions,
        rng=FixedDice(),
        authorized_actor_id="a",
        system=True,
    )
    assert updated.pools[0].current == 4
    assert outcome.injury is not None and outcome.injury.injury == 1
    restarted = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_physiology_interval(
        restarted,
        request,
        interval,
        build,
        engine.definitions,
        rng=FixedDice(),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, outcome)
    alias = interval.model_copy(update={"id": "alias"})
    with pytest.raises(ConflictError, match="consumed"):
        apply_physiology_interval(
            restarted,
            command(1, "alias"),
            alias,
            build,
            engine.definitions,
            rng=FixedDice(),
            authorized_actor_id="a",
            system=True,
        )
    following = interval.model_copy(update={"id": "next", "due": due + cadence})
    next_state, _ = apply_physiology_interval(
        updated.model_copy(update={"game_time": due + cadence}),
        command(1, "next"),
        following,
        build,
        engine.definitions,
        rng=FixedDice(),
        authorized_actor_id="a",
        system=True,
    )
    assert next_state.pools[0].current == 3


def test_weakness_canonical_death_and_ended_exposure() -> None:
    build, engine = approved(
        Purchase(
            definition_id="disadvantage:weakness", trait=options(rarity="common", interval="minute")
        )
    )
    interval = PhysiologyInterval(id="weak", actor_id="a", kind="weakness", due=60)
    updated, outcome = apply_physiology_interval(
        state(current=-49),
        command(identifier="weak"),
        interval,
        build,
        engine.definitions,
        rng=FixedDice(),
        authorized_actor_id="a",
        system=True,
    )
    assert updated.pools[0].current == -52
    assert updated.pools[0].injury is not None and updated.pools[0].injury.dead
    assert outcome.damage_dice == (3,) and outcome.injury is not None
    ended, unavailable = apply_physiology_interval(
        state(),
        command(identifier="weak"),
        interval.model_copy(update={"active": False}),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert unavailable.kind == "unavailable" and ended.pools == state().pools


@pytest.mark.parametrize("level", ["partial", "total"])
@pytest.mark.parametrize("kind", ["natural", "bandage", "first-aid", "physician", "drug"])
def test_unhealing_canonical_recovery(level: str, kind: str) -> None:
    build, engine = approved(
        Purchase(definition_id="disadvantage:unhealing", trait=options(kind=level))
    )
    resources = state()
    ordinary, amount = restore_hp(resources, resources.pools[0], 3, kind=kind)
    assert ordinary.current == 8 and amount == 3
    blocked, amount = restore_hp(
        resources,
        resources.pools[0],
        3,
        kind=kind,
        physiology=physiology_traits(build, engine.definitions),
    )
    assert blocked == resources.pools[0] and amount == 0


def test_partial_unhealing_condition_and_magical_exception() -> None:
    resources = state()
    for level in ("partial", "total"):
        build, engine = approved(
            Purchase(definition_id="disadvantage:unhealing", trait=options(kind=level))
        )
        traits = physiology_traits(build, engine.definitions)
        healed, amount = restore_hp(
            resources,
            resources.pools[0],
            3,
            kind="natural",
            physiology=traits,
            unhealing_condition=True,
        )
        assert healed.current == (8 if level == "partial" else 5)
        healed, amount = restore_hp(
            resources, resources.pools[0], 3, kind="magical", physiology=traits
        )
        assert healed.current == 8 and amount == 3
        healed, amount = restore_hp(
            resources, resources.pools[0], 3, kind="steal-hp", physiology=traits
        )
        assert healed.current == (8 if level == "partial" else 5)


def test_regeneration_cannot_bypass_total_unhealing() -> None:
    build, engine = approved(
        Purchase(definition_id="disadvantage:unhealing", trait=options(kind="total")),
        Purchase(definition_id="advantage:regeneration", trait=options(rate="fast")),
    )
    updated, result = apply_physiology_interval(
        state(),
        command(),
        PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=60),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert updated.pools[0].current == 5 and result.hp_after == 5


@pytest.mark.parametrize("level", ["partial", "total"])
def test_unhealing_medical_entry_and_completion(level: str) -> None:
    build, engine = approved(
        Purchase(definition_id="disadvantage:unhealing", trait=options(kind=level))
    )
    traits = physiology_traits(build, engine.definitions)
    resources = state().model_copy(
        update={
            "pools": state().pools
            + (Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),)
        }
    )
    request = BeginRecovery(
        id="natural", actor_id="a", expected_revision=0, kind="natural", target_id="a"
    )
    with pytest.raises(ValidationError, match="Unhealing"):
        apply_recovery(
            resources,
            request,
            CareContext(PROFILE, 10, physiology=traits),
            rng=FixedDice(),
            system=True,
        )
    pending, _ = apply_recovery(
        resources, request, CareContext(PROFILE, 10, food=True), rng=FixedDice(), system=True
    )
    finish = FinishRecovery(id="finish", actor_id="a", expected_revision=1, task_id="natural")
    pending = pending.model_copy(update={"game_time": pending.recovery_tasks[0].due})
    updated, result = apply_recovery(
        pending, finish, CareContext(PROFILE, 10, physiology=traits), rng=FixedDice(), system=True
    )
    assert updated.pools[0].current == 5 and result.hp_recovered == 0
    restarted = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_recovery(
        restarted, finish, CareContext(PROFILE, 10, physiology=traits), rng=FixedDice(), system=True
    ) == (restarted, result)
