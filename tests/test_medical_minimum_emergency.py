"""Actual B424-425 emergency care boundaries, with B345 on the final score."""

import secrets
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign, world
from test_default_knowledge_hosts import adopt
from test_medical_minimum import PROFILE, replay, revision
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.medicine import definitions
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical import CareEnvironment, MedicalService, _build
from wayfarer.orchestration.play import PlayService


async def clinic(
    path: Path, backend: str, iq: int, purchases: tuple[Purchase, ...], *, hp: int | None = None
) -> tuple[str, PlayService]:
    names = {"skill:first-aid", "skill:physician", "skill:surgery", "skill:veterinary"}
    package = profile_package(PROFILE, *(d for d in definitions() if d.id in names))
    base = profile_compiler(PROFILE)
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),)),
        replace(base.policy, technology_level=8),
        statistics_profile=PROFILE,
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="medical-emergency", version=1), frozenset({"gm"})),
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        ActionRules(id="emergency-boundaries", version=1, maximum_wait=10000),
    )
    play = build_play(path, engine, backend=backend, seeds=lambda: f"{270:064x}")
    doctor = gurps_draft(*purchases)
    doctor = doctor.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": iq}) if p.definition_id == "attribute:iq" else p
                for p in doctor.purchases
            )
        }
    )
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world(),
        ResourceState(
            owners=(
                Owner(actor_id="a", capacity=100),
                Owner(actor_id="b", capacity=100),
            )
        ),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=doctor),
                body=HumanBody(anatomy="human"),
            ),
            ActorSetup(
                actor_id="b",
                proposal=CharacterProposal(draft=gurps_draft()),
                body=HumanBody(anatomy="human"),
            ),
        ),
    )
    pools = []
    for pool in state.resources.pools:
        if pool.id == "hp:b" and hp is not None:
            assert pool.injury is not None
            pool = pool.model_copy(
                update={
                    "current": hp,
                    "injury": pool.injury.model_copy(
                        update={
                            "mortal_wound": True,
                            "mortal_wound_started": 0,
                            "mortal_wound_due": 1800,
                            "unconscious": True,
                        }
                    ),
                }
            )
        elif pool.id == "fp:b" and hp is None:
            assert pool.fatigue is not None
            pool = pool.model_copy(
                update={
                    "fatigue": pool.fatigue.model_copy(
                        update={
                            "heart_attack": True,
                            "heart_attack_deadline": 300,
                            "unconscious": True,
                        }
                    )
                }
            )
        pools.append(pool)
    # Retained, source-shaped initial medical conditions; every treatment and
    # survival check below is performed through the real service and store.
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"pools": tuple(pools)})}
    )
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    await adopt(play, initial["id"], tuple(sorted(names)))
    return initial["id"], play


def medical(play: PlayService) -> MedicalService:
    # B424: TL8 stabilization gets +2; no anesthetic gives -2. The numeric
    # surgical_modifier remains its ordinary zero, never a threshold override.
    return MedicalService(
        play,
        lambda *_: CareEnvironment(
            technology_level=8,
            surgical_facility=True,
            anesthetic=False,
        ),
    )


async def advance(play: PlayService, cid: str, ticks: int, identifier: str) -> None:
    await play.execute(
        cid,
        Wait(id=identifier, actor_id="a", expected_revision=await revision(play, cid), ticks=ticks),
        principal_id="a",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("route", ["first-aid", "physician"])
@pytest.mark.parametrize("target", [2, 3])
async def test_cpr_uses_first_aid_minus_four_or_physician_before_boundary_admission(
    tmp_path: Path, backend: str, route: Literal["first-aid", "physician"], target: int
) -> None:
    # Paid Easy First Aid at IQ6/7 gives 6/7-4=2/3. Alternatively IQ9/10
    # gives Physician defaults 2/3, beating First Aid defaults 5/6-4=1/2.
    iq = target + (4 if route == "first-aid" else 7)
    purchases = (
        (Purchase(definition_id="skill:first-aid", amount=1, technology_level=8),)
        if route == "first-aid"
        else ()
    )
    cid, play = await clinic(tmp_path, backend, iq, purchases)
    state = play._load(await play.store.read(cid))
    values = {v.target: int(v.value) for v in _build(play, state, "a").sheet.values}
    assert values["skill:first-aid"] == (target + 4 if route == "first-aid" else target + 3)
    if route == "physician":
        assert values["skill:physician"] == target
    command = BeginRecovery(
        id="cpr", actor_id="a", expected_revision=state.revision, kind="resuscitate", target_id="b"
    )
    before = await play.store.read(cid)
    if target == 2:
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="at least 3"):
            await medical(play).execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before and play.rng.exhausted()
        assert await play.store.command_input(cid, command.id) is None
        return
    pending = await medical(play).execute(cid, command, principal_id="a")
    assert pending.status == "pending"
    task = play._load(await play.store.read(cid)).resources.recovery_tasks[-1]
    assert task.skill == 3 and task.treatment_modifier == 0 and task.due - task.start == 60
    await advance(play, cid, 60, "wait-cpr")
    finish = FinishRecovery(
        id="finish", actor_id="a", expected_revision=await revision(play, cid), task_id="cpr"
    )
    done = await medical(play).execute(cid, finish, principal_id="a")
    assert (
        done.check
        and done.check.effective_target == 3
        and done.check.total == 3
        and done.resuscitated
    )
    patient = next(
        p for p in play._load(await play.store.read(cid)).resources.pools if p.id == "fp:b"
    )
    assert patient.fatigue and not patient.fatigue.heart_attack
    assert await medical(play).execute(cid, finish, principal_id="a") == done
    await replay(play, cid, tmp_path / "cpr-seed-replay")


async def survival(play: PlayService, cid: str, identifier: str) -> None:
    play.rng = RecordedDice((3, 3, 3))
    result = await medical(play).execute(
        cid,
        BeginRecovery(
            id=identifier,
            actor_id="b",
            expected_revision=await revision(play, cid),
            kind="mortal-check",
            target_id="b",
        ),
        principal_id="b",
    )
    assert result.check and result.check.total == 9 and result.check.outcome.succeeded
    assert not result.stabilized and play.rng.exhausted()
    play.rng = secrets


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hp,band", [(-30, -2), (-40, -4)])
@pytest.mark.parametrize("target", [2, 3])
async def test_stabilization_combines_tl_hp_repeat_and_anesthetic_before_floor(
    tmp_path: Path, backend: str, hp: int, band: int, target: int
) -> None:
    # VH Surgery at IQ-3; TL8 +2, no anesthetic -2, printed HP band -2/-4,
    # and one actual completed failed attempt -2 give final2/final3.
    raw = target - band + 2
    cid, play = await clinic(
        tmp_path,
        backend,
        raw + 3,
        (
            Purchase(definition_id="skill:first-aid", amount=1, technology_level=8),
            Purchase(definition_id="skill:surgery", amount=1, technology_level=8),
        ),
        hp=hp + 3,
    )
    prior = BeginRecovery(
        id="prior-surgery",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="stabilize",
        target_id="b",
    )
    await medical(play).execute(cid, prior, principal_id="a")
    await advance(play, cid, 1800, "prior-wait-1")
    await survival(play, cid, "prior-survival-1")
    await advance(play, cid, 1800, "prior-wait-2")
    await survival(play, cid, "prior-survival-2")
    play.rng = RecordedDice((4, 4, 4, 1, 1, 1, 3, 3, 3))
    failed = await medical(play).execute(
        cid,
        FinishRecovery(
            id="prior-finish",
            actor_id="a",
            expected_revision=await revision(play, cid),
            task_id="prior-surgery",
        ),
        principal_id="a",
    )
    assert failed.check and failed.check.total == 12 and not failed.check.outcome.succeeded
    assert failed.hp_recovered == -3 and play.rng.exhausted()
    play.rng = secrets
    state = play._load(await play.store.read(cid))
    patient = next(p for p in state.resources.pools if p.id == "hp:b")
    assert patient.current == hp and state.resources.recovery_tasks[-1].status == "completed"
    assert {v.target: int(v.value) for v in _build(play, state, "a").sheet.values}[
        "skill:surgery"
    ] == raw
    command = BeginRecovery(
        id="repeat-surgery",
        actor_id="a",
        expected_revision=state.revision,
        kind="stabilize",
        target_id="b",
    )
    before = await play.store.read(cid)
    if target == 2:
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="at least 3"):
            await medical(play).execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before and play.rng.exhausted()
        assert await play.store.command_input(cid, command.id) is None
        return
    await medical(play).execute(cid, command, principal_id="a")
    task = play._load(await play.store.read(cid)).resources.recovery_tasks[-1]
    assert task.skill == raw and task.treatment_modifier == band - 2
    assert task.skill + task.treatment_modifier == 3 and task.due - task.start == 3600
    await advance(play, cid, 1800, "repeat-wait-1")
    await survival(play, cid, "repeat-survival-1")
    await advance(play, cid, 1800, "repeat-wait-2")
    await survival(play, cid, "repeat-survival-2")
    finish = FinishRecovery(
        id="finish",
        actor_id="a",
        expected_revision=await revision(play, cid),
        task_id="repeat-surgery",
    )
    done = await medical(play).execute(cid, finish, principal_id="a")
    assert (
        done.check
        and done.check.effective_target == 3
        and done.check.total == 3
        and done.stabilized
    )
    patient = next(
        p for p in play._load(await play.store.read(cid)).resources.pools if p.id == "hp:b"
    )
    assert patient.current == hp and patient.injury and not patient.injury.mortal_wound
    assert (
        patient.injury.unconscious
        and await medical(play).execute(cid, finish, principal_id="a") == done
    )
    # The old mandatory survival producer did not capture its environment. Seed
    # reexecute the genuine accepted surgical continuation after that exact receipt.
    await replay(play, cid, tmp_path / "surgery-continuation-replay", after="repeat-survival-2")
