"""B421/B428 corrections select captured rules, never reinterpret old checks."""

import asyncio
import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, open_store, played, seed_campaign
from test_basic_combat import start_basic
from test_combat_sensory_authority import hearing
from test_composed_attack_host import declare, defense, fixture
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_symptoms import selection

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.commands import EndEncounter, TakeCombatTurn
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.condition_generations import current_attributes
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
from wayfarer.engine.simulation.health.toxins import DrinkCommand
from wayfarer.engine.simulation.traits.composed_sources import BindComposedSource
from wayfarer.engine.simulation.traits.harmful_physiology_state import AdvancePhysiology
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.combat_senses import CombatSensesService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.drinking import DrinkingEnvironment, DrinkingService
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.orchestration.medical import CareEnvironment, MedicalService
from wayfarer.orchestration.symptom_generations import symptom_generation
from wayfarer.persistence.command_inputs import KEY, replay_payload, stamp
from wayfarer.persistence.replay import command_text, verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [0, 1, 2, 3])
async def test_real_coughing_source_changes_hearing_only_for_its_captured_generation(
    tmp_path: Path, backend: str, version: int
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        modifiers=(selection("coughing", "one-third"),),
        levels=1,
        skill_points=16,
        bind_source=False,
    )
    initial = await play.store.read(cid)
    # Source binding, paid attack and condition onset are reexecuted too: the
    # seed's ordinary hit9 and damage4 exceed one-third of actual HP10.
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    bound = await ComposedAttackService(play).execute(
        cid,
        BindComposedSource(
            id="bind-coughing",
            actor_id="a",
            expected_revision=initial["revision"],
            description="The approved burning attack causes coughing after sufficient injury",
            specialty="beam",
        ),
        principal_id="gm",
    )
    assert bound.source_id is not None
    await declare(play, cid, bound.source_id)
    _, attack = await defense(play, cid)
    assert attack.injury and attack.injury.injury == 4
    before = await play.store.read(cid)
    state = play._load(before)
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 6
    assert state.resources.symptom_effects[0].active
    assert sum(d.remaining for d in state.resources.symptom_debts) == 4
    command = hearing(state.revision)
    service = CombatSensesService(play)
    with pytest.raises((AuthorizationError, ValidationError)):
        await service.execute(cid, command, principal_id="bob")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="gm"
        )
    assert await play.store.read(cid) == before
    play.seeds = lambda: f"{10:064x}"  # Actual Hearing total8.
    if version == 3:
        result = await service.execute(cid, command, principal_id="gm")
    else:
        # Manufacture a historical input through its original reducer, never
        # by changing a completed receipt or using the corrected target as oracle.
        plan = service.plan(play, state, command, principal_id="gm")
        payload = plan.payload if version == 0 else stamp(plan.payload, version=version)
        with symptom_generation(version > 0, condition_attributes=version >= 2):
            await commit_command(
                play,
                cid,
                command.id,
                command.expected_revision,
                payload,
                plan.resolve,
                actor_id="gm",
                rng=play.rng,
            )
        result = await plan.outcome(await play.store.read(cid))
    assert result and result.hearing
    assert result.hearing.effective_target == (7 if version >= 2 else 8)
    assert result.hearing.total == 8 and result.located == (version < 2)
    recorded = (await play.store.history(cid))[-1]
    payload = json.loads(command_text(recorded))
    assert payload.get(KEY, 0) == version
    decoded = replay_payload(command_text(recorded))
    assert isinstance(decoded, dict) and decoded["operation"] == "combat-senses"
    final = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatSensesService(restarted).execute(cid, command, principal_id="gm") == result
    assert await play.store.read(cid) == final
    records = [
        r for r in await played(play.store, cid) if r.expected_revision >= initial["revision"]
    ]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_source_specific_healing_removes_coughing_penalty_and_restores_location(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source_id = await fixture(
        tmp_path,
        backend,
        modifiers=(selection("coughing", "one-third"),),
        levels=1,
        skill_points=16,
    )
    original = play._load(await play.store.read(cid))
    await declare(play, cid, source_id)
    play.rng = RecordedDice((3, 3, 3, 4))
    await defense(play, cid)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 3, 3))
    first = await CombatSensesService(play).execute(cid, hearing(state.revision), principal_id="gm")
    assert first and first.hearing and first.hearing.effective_target == 7 and not first.located
    state = play._load(await play.store.read(cid))
    assert check_modifiers(state.resources, "b", "will", defensive=True) == ()
    assert check_modifiers(state.resources, "b", "per", defensive=True) == ()
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            reason="Rest",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    medical = MedicalService(play, lambda *_: CareEnvironment(food=True, water=True))
    await medical.execute(
        cid,
        BeginRecovery(
            id="day", actor_id="b", target_id="b", kind="natural", expected_revision=state.revision
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await HarmfulPhysiologyService(play).execute(
        cid,
        AdvancePhysiology(
            id="rest",
            actor_id="b",
            expected_revision=state.revision,
            to=state.resources.recovery_tasks[-1].due,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    healed = await medical.execute(
        cid,
        FinishRecovery(id="heal", actor_id="b", expected_revision=state.revision, task_id="day"),
        principal_id="b",
    )
    assert healed.hp_recovered == 1
    state = play._load(await play.store.read(cid))
    assert not state.resources.symptom_effects[0].active
    assert sum(d.remaining for d in state.resources.symptom_debts) == 3
    assert check_modifiers(state.resources, "b", "will") == ()
    assert check_modifiers(state.resources, "b", "per") == ()
    assert state.actors == original.actors
    opening = start_basic(2, ranged=True)
    await CombatService(play).execute(
        cid,
        opening.model_copy(
            update={
                "id": "again",
                "encounter_id": "second",
                "expected_revision": state.revision,
                "facts": tuple(
                    fact.model_copy(
                        update={
                            "provenance": fact.provenance.model_copy(
                                update={"declared_revision": state.revision}
                            )
                        }
                    )
                    for fact in opening.facts
                ),
            }
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 3, 3))
    second = await CombatSensesService(play).execute(
        cid,
        hearing(state.revision, "after-healing").model_copy(update={"encounter_id": "second"}),
        principal_id="gm",
    )
    assert second and second.hearing and second.hearing.effective_target == 8 and second.located
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_condition_generation_resets_and_is_task_local() -> None:
    entered, resume = asyncio.Event(), asyncio.Event()

    async def old() -> None:
        with pytest.raises(RuntimeError), symptom_generation(True):
            assert not current_attributes()
            entered.set()
            await resume.wait()
            assert not current_attributes()
            raise RuntimeError("failed historical command")
        assert current_attributes()

    async def current() -> None:
        await entered.wait()
        assert current_attributes()
        with symptom_generation(True, condition_attributes=True):
            assert current_attributes()
        resume.set()

    await asyncio.gather(old(), current())
    assert current_attributes()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [1, 2])
async def test_actual_drinking_preserves_feint_resistance_and_changes_later_parry(
    tmp_path: Path, backend: str, version: int
) -> None:
    cid, play = await setup(tmp_path / "setup", "gurps-basic-set-4e-2004")
    if backend == "postgres":
        store = open_store(tmp_path / "source", backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    drinks = DrinkingService(play, lambda *_: DrinkingEnvironment())
    play.rng = RecordedDice((5, 5, 5, 5, 5, 5, 3, 3, 3))
    for index, count in enumerate((4, 1)):
        state = play._load(await play.store.read(cid))
        await drinks.execute(
            cid,
            DrinkCommand(
                id=f"drink-{index}",
                actor_id="b",
                expected_revision=state.revision,
                kind="drink",
                drinks=count,
            ),
            principal_id="b",
        )
    before = await play.store.read(cid)
    state = play._load(before)
    assert state.resources.intoxications[0].level == "drunk" and play.rng.exhausted()
    command = TakeCombatTurn(
        id="feint",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="feint",
        item_id="sword-a",
        mode_id="swing",
        target_id="b",
    )
    combat = CombatService(play)
    play.rng = RecordedDice((3, 3, 3, 4, 4, 4))
    if version == 2:
        await combat.execute(cid, command, principal_id="a")
    else:
        plan = combat.plan(cid, command)
        with symptom_generation(True):
            await commit_command(
                play,
                cid,
                command.id,
                command.expected_revision,
                stamp(plan.payload, version=1),
                plan.resolve,
                actor_id="a",
                rng=play.rng,
            )
    state = play._load(await play.store.read(cid))
    commitment = state.encounters[0].participants[0].maneuver_state
    assert commitment.feint_rolls[0].effective_target == 13
    assert commitment.feint_rolls[1].effective_target == (13 if version == 2 else 11)
    assert commitment.feint_penalty == (3 if version == 2 else 4)
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice((3, 3, 3, 2, 2, 3) + ((2,) if version == 1 else ()))
    settled = await defend(cid, play, "b", "parry", item_id="sword-b")
    assert settled.injury and settled.injury.defense
    assert settled.injury.defense.effective_target == (7 if version == 2 else 6)
    assert settled.injury.injury == (0 if version == 2 else 4)
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)
