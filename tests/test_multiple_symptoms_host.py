"""B109/B424/B426: purchased Symptoms, actual host commands, healing and receipts."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_combat_sensory_authority import change
from test_composed_attack_host import declare, defense, fixture, idle
from test_multiple_symptoms import EFFECTS, cyclic_selection

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.cyclic_runtime import additional_symptoms
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ChooseDefense, EndEncounter
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy, ObserveCyclicStop
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.traits.composed_sources import BindComposedSource
from wayfarer.engine.simulation.traits.harmful_physiology_state import AdvancePhysiology
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.orchestration.medical import CareEnvironment, MedicalService
from wayfarer.orchestration.play import ApproveCharacter
from wayfarer.orchestration.replay import execute_recorded


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_purchased_multi_symptom_cyclic_hit_and_source_bound_natural_recovery(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(
        tmp_path,
        backend,
        modifiers=(cyclic_selection(),) + EFFECTS,
        levels=1,
        cyclic_policy=CyclicPolicy(condition="wash"),
    )
    initial = play._load(await play.store.read(cid))
    attack = await declare(play, cid, source)
    after_declaration = await play.store.read(cid)
    with pytest.raises(AuthorizationError):
        await defense(play, cid, principal="alice")
    assert await play.store.read(cid) == after_declaration
    play.rng = RecordedDice([2, 2, 2, 4])
    defense_command, result = await defense(play, cid)
    state = play._load(await play.store.read(cid))
    assert result.injury and result.injury.injury == 4
    assert state.actors == initial.actors
    assert sum(m.value for m in check_modifiers(state.resources, "b", "dx")) == -3
    assert not acute_blindness(state.resources, "b")
    assert play.rng.exhausted()
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="stop-combat",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            reason="Combatants disengage",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([4])
    tick = AdvancePhysiology(
        id="cyclic-tick", actor_id="b", expected_revision=state.revision, to=10
    )
    await HarmfulPhysiologyService(play).execute(cid, tick, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert play.rng.exhausted()
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 2
    assert acute_blindness(state.resources, "b")
    assert len(state.resources.symptom_effects) == 2
    assert len(state.resources.symptom_debts) == 2
    assert sum(d.remaining for d in state.resources.symptom_debts) == 8
    occurrence = state.resources.cyclic_attacks[0]
    stop = ObserveCyclicStop(
        id="wash",
        actor_id="b",
        expected_revision=state.revision,
        occurrence_id=occurrence.id,
        location_id="dock",
        reason="Observed washing the source-approved substance away",
    )
    await CyclicService(play).execute(cid, stop, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert acute_blindness(state.resources, "b")  # Stopping further injury does not heal it.
    service = MedicalService(play, lambda *_: CareEnvironment(food=True, water=True))
    recoveries: list[tuple[BeginRecovery, AdvancePhysiology, FinishRecovery]] = []
    for day in range(1, 6):
        state = play._load(await play.store.read(cid))
        start = BeginRecovery(
            id=f"day-{day}",
            actor_id="b",
            target_id="b",
            kind="natural",
            expected_revision=state.revision,
        )
        await service.execute(cid, start, principal_id="b")
        state = play._load(await play.store.read(cid))
        task = state.resources.recovery_tasks[-1]
        wait = AdvancePhysiology(
            id=f"wait-{day}", actor_id="b", expected_revision=state.revision, to=task.due
        )
        await HarmfulPhysiologyService(play).execute(cid, wait, principal_id="gm")
        state = play._load(await play.store.read(cid))
        finish = FinishRecovery(
            id=f"finish-{day}", actor_id="b", expected_revision=state.revision, task_id=start.id
        )
        play.rng = RecordedDice([2, 2, 2])
        recovered = await service.execute(cid, finish, principal_id="b")
        state = play._load(await play.store.read(cid))
        assert recovered.hp_recovered == 1 and play.rng.exhausted()
        assert acute_blindness(state.resources, "b") is (day == 1)
        assert sum(m.value for m in check_modifiers(state.resources, "b", "dx")) == (
            -3 if day < 5 else 0
        )
        recoveries.append((start, wait, finish))
    require_hazard_capacity(state.resources, "b", "stealth")
    require_hazard_capacity(state.resources, "b", "vision")
    assert state.actors == initial.actors
    final = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice([]))
    await ComposedAttackService(restarted).execute(cid, attack, principal_id="alice")
    assert (
        await CombatService(restarted).execute(cid, defense_command, principal_id="bob") == result
    )
    await HarmfulPhysiologyService(restarted).execute(cid, tick, principal_id="gm")
    await CyclicService(restarted).execute(cid, stop, principal_id="gm")
    retry_medical = MedicalService(
        restarted,
        lambda *_: (_ for _ in ()).throw(AssertionError("retry must retain its care context")),
    )
    for start, wait, finish in recoveries:
        await retry_medical.execute(cid, start, principal_id="b")
        await HarmfulPhysiologyService(restarted).execute(cid, wait, principal_id="gm")
        await retry_medical.execute(cid, finish, principal_id="b")
    with pytest.raises(ConflictError):
        await ComposedAttackService(restarted).execute(
            cid, attack.model_copy(update={"target_id": "a"}), principal_id="alice"
        )
    with pytest.raises(ConflictError):
        await ComposedAttackService(restarted).execute(
            cid, attack.model_copy(update={"id": "stale"}), principal_id="alice"
        )
    with pytest.raises(AuthorizationError):
        await CombatService(restarted).execute(cid, defense_command, principal_id="alice")
    assert await restarted.store.read(cid) == final == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seed_only_reexecution_of_multi_symptom_delivery(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(
        tmp_path / "original", backend, modifiers=EFFECTS, levels=1, skill_points=16
    )
    await declare(play, cid, source)
    before = await play.store.read(cid)
    state = play._load(before)
    chosen = ChooseDefense(
        id="seeded",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    play.rng = secrets
    await CombatService(play).execute(cid, chosen, principal_id="bob")
    recorded = (await play.store.history(cid))[-1]
    assert recorded.reexecutable
    after = play._load(await play.store.read(cid))
    assert len(after.resources.symptom_effects) == 2
    replay = build_play(tmp_path / "replay", play.engine, rng=secrets)
    await seed_campaign(replay.store, before)
    await execute_recorded(replay, recorded)
    assert replay._load(await replay.store.read(cid)) == after
    assert (await replay.store.history(cid))[-1].event == recorded.event
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_purchased_multi_symptoms_critical_self_hit_binds_actual_source_and_limb(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.health.cyclic_host_state import binding
    from wayfarer.engine.simulation.traits.composed_resolution import (
        RESOLUTION_PREFIX,
        ComposedResolution,
    )
    from wayfarer.engine.simulation.traits.innate_criticals import innate_critical_outcome

    cid, play, source = await fixture(
        tmp_path,
        modifiers=(cyclic_selection(),) + EFFECTS,
        levels=1,
        cyclic_policy=CyclicPolicy(condition="wash"),
    )
    attack = await declare(play, cid, source)
    # B556: miss18, table5 rerolls once to5, right arm, full damage4. B109 adds no roll.
    play.rng = RecordedDice([6, 6, 6, 1, 1, 3, 1, 1, 3, 1, 1, 4])
    chosen, result = await defense(play, cid)
    state = play._load(await play.store.read(cid))
    resolution = next(
        ComposedResolution.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith(RESOLUTION_PREFIX)
    )
    assert resolution.critical_id
    critical = innate_critical_outcome(state.resources, resolution.critical_id)
    assert critical.location == "right-arm" and critical.injury == 4 and critical.cyclic_attack_id
    occurrence = state.resources.cyclic_attacks[0]
    bound = binding(state.resources, occurrence.id)
    assert bound and bound.location == "right-arm" and bound.source_id == source
    assert (
        occurrence.actor_id == "a"
        and occurrence.hp_debt == 4
        and len(additional_symptoms(occurrence)) == 1
    )
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 6
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert [e.active for e in state.resources.symptom_effects] == [True, False]
    assert all(
        e.actor_id == "a" and e.source_id == "a:" + source for e in state.resources.symptom_effects
    )
    assert (
        len(state.resources.symptom_debts) == 1 and state.resources.symptom_debts[0].remaining == 4
    )
    assert sum(m.value for m in check_modifiers(state.resources, "a", "dx")) == -3
    assert play.rng.exhausted()
    final = await play.store.read(cid)
    await ComposedAttackService(play).execute(cid, attack, principal_id="alice")
    assert await CombatService(play).execute(cid, chosen, principal_id="bob") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_persisted_reapproved_reordered_effects_continue_the_same_damage_source(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend, modifiers=EFFECTS, levels=1)
    await declare(play, cid, source)
    play.rng = RecordedDice([2, 2, 2, 2])
    _, result = await defense(play, cid)
    assert result.injury and result.injury.injury == 2 and play.rng.exhausted()
    original = play._load(await play.store.read(cid))
    await idle(play, cid)

    def reorder(state: PlayState) -> PlayState:
        actor = next(a for a in state.actors if a.actor_id == "a")
        draft = actor.proposal.draft
        purchases = tuple(
            p.model_copy(
                update={
                    "trait": p.trait.model_copy(
                        update={"attack_modifiers": tuple(reversed(p.trait.attack_modifiers))}
                    )
                }
            )
            if p.definition_id == "advantage:innate-attack" and p.trait is not None
            else p
            for p in draft.purchases
        )
        proposal = actor.proposal.model_copy(
            update={"draft": draft.model_copy(update={"purchases": purchases})}
        )
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"proposal": proposal, "approval": None})
                    if a.actor_id == "a"
                    else a
                    for a in state.actors
                )
            }
        )

    await change(play, cid, reorder)
    state = play._load(await play.store.read(cid))
    await play.approve(
        cid,
        ApproveCharacter(
            id="approve-reorder",
            actor_id="gm",
            target_actor_id="a",
            expected_revision=state.revision,
            reason="Same attack, reordered authored effects",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    rebound = await ComposedAttackService(play).execute(
        cid,
        BindComposedSource(
            id="rebind",
            actor_id="a",
            expected_revision=state.revision,
            description="A directed natural burning beam",
            specialty="beam",
        ),
        principal_id="gm",
    )
    assert rebound.source_id == source
    attack = await declare(play, cid, source, identifier="second-attack")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([2, 2, 2, 2])
    chosen = ChooseDefense(
        id="second-defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, chosen, principal_id="bob")
    assert result.injury and result.injury.injury == 2 and play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 6
    assert tuple((e.id, e.spec) for e in state.resources.symptom_effects) == tuple(
        (e.id, e.spec) for e in original.resources.symptom_effects
    )
    assert (
        state.resources.symptom_debts[: len(original.resources.symptom_debts)]
        == original.resources.symptom_debts
    )
    assert (
        len(state.resources.symptom_debts) == 2
        and sum(d.remaining for d in state.resources.symptom_debts) == 4
    )
    assert [e.active for e in state.resources.symptom_effects] == [True, False]
    assert sum(m.value for m in check_modifiers(state.resources, "b", "dx")) == -3
    final = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice([]))
    await ComposedAttackService(restarted).execute(cid, attack, principal_id="alice")
    assert await CombatService(restarted).execute(cid, chosen, principal_id="bob") == result
    assert await restarted.store.read(cid) == final == await restarted.store.replay(cid)
