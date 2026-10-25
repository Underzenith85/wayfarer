"""Actual B345 care admission with source B424 targets and retained producers."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_campaign
from test_actions import campaign, world
from test_default_knowledge_hosts import adopt
from test_statistics import gurps_draft, profile_compiler, profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.skills.mundane.medicine import definitions
from wayfarer.engine.rules.types.location import HumanBody, LastingInjury
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.medical import minimum
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
from wayfarer.engine.simulation.health.toxins import DrinkCommand
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration import medical_minimum_generations as generations
from wayfarer.orchestration.drinking import DrinkingEnvironment, DrinkingService
from wayfarer.orchestration.medical import CareEnvironment, MedicalService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.projections import ViewRequest, project
from wayfarer.persistence.replay import verify_commands

PROFILE = "gurps-basic-set-4e-2004"


async def fixture(
    path: Path,
    backend: str,
    iq: int = 7,
    *,
    trained: bool = False,
    surgical: bool = False,
    ht: int = 10,
) -> tuple[str, PlayService]:
    names = (
        {"skill:first-aid", "skill:physician", "skill:surgery", "skill:veterinary"}
        if surgical
        else {"skill:first-aid"}
    )
    package = profile_package(PROFILE, *(d for d in definitions() if d.id in names))
    base = profile_compiler(PROFILE)
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),)),
        replace(base.policy, technology_level=7 if surgical else 8),
        statistics_profile=PROFILE,
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="medical", version=1), frozenset({"gm"})),
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        ActionRules(id="medical-minimum", version=1, maximum_wait=10000),
    )
    play = build_play(path, engine, backend=backend, seeds=lambda: f"{270:064x}")
    extra = (
        (Purchase(definition_id="skill:first-aid", amount=1, technology_level=8),)
        if trained
        else ()
    )
    if surgical:
        extra = (Purchase(definition_id="skill:physician", amount=4, technology_level=7),)
    draft = gurps_draft(*extra)
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": iq})
                if p.definition_id == "attribute:iq"
                else p.model_copy(update={"amount": ht})
                if p.definition_id == "attribute:ht"
                else p
                for p in draft.purchases
            )
        }
    )
    initial = campaign(engine)
    actors: tuple[ActorSetup, ...] = (
        ActorSetup(actor_id="a", proposal=CharacterProposal(draft=draft)),
    )
    if surgical:
        actors += (
            ActorSetup(
                actor_id="b",
                proposal=CharacterProposal(draft=gurps_draft()),
                body=HumanBody(anatomy="human"),
            ),
        )
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=tuple(Owner(actor_id=a.actor_id, capacity=100) for a in actors)),
        actors,
    )
    resources, _ = apply_injury(
        state.resources,
        Wound(
            id="injury",
            actor_id="a",
            expected_revision=0,
            basic_damage=4,
            resistance=0,
            damage_type="cr",
        ),
        ht=ht,
        rng=RecordedDice(()),
        system=True,
    )
    if surgical:
        pools = []
        for pool in resources.pools:
            if pool.id == "hp:b":
                assert pool.injury is not None
                pool = pool.model_copy(
                    update={
                        "injury": pool.injury.model_copy(
                            update={
                                "lasting_injuries": (
                                    LastingInjury(
                                        id="leg",
                                        location="left-leg",
                                        kind="crippled",
                                        duration="lasting",
                                        inflicted_at=0,
                                        injury=6,
                                        recovery_at=30 * 86400,
                                    ),
                                ),
                            }
                        )
                    }
                )
            pools.append(pool)
        resources = resources.model_copy(update={"pools": tuple(pools)})
    # A native damage result forms the retained genesis; treatment below uses actual services.
    state = state.model_copy(update={"revision": resources.revision, "resources": resources})
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    await adopt(play, initial["id"], tuple(sorted(names)))
    return initial["id"], play


def service(play: PlayService) -> MedicalService:
    return MedicalService(play, lambda *_: CareEnvironment(technology_level=8))


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def begin(play: PlayService, cid: str) -> BeginRecovery:
    return BeginRecovery(
        id="aid",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="first-aid",
        target_id="a",
        wound_id="injury:injury",
    )


async def wait(play: PlayService, cid: str, seconds: int) -> None:
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=await revision(play, cid), ticks=seconds),
        principal_id="a",
    )


async def replay(play: PlayService, cid: str, path: Path, *, after: str | None = None) -> None:
    initial = (await play.store.history(cid))[0].state_after
    commands = await played(play.store, cid)
    if after is not None:
        index = next(i for i, c in enumerate(commands) if c.command_id == after)
        initial = commands[index].state_after
        commands = commands[index + 1 :]
    ids = {c.command_id for c in commands}
    actual, checks = await verify_commands(
        initial,
        commands,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, path),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert actual == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_source_first_aid_two_refuses_before_external_environment_or_entropy(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, 6)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())

    def forbidden(*_: object) -> CareEnvironment:
        raise AssertionError("Known final2 must refuse before external environment capture")

    with pytest.raises(ValidationError, match="at least 3"):
        await MedicalService(play, forbidden).execute(cid, await begin(play, cid), principal_id="a")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trained", [False, True])
async def test_source_three_or_paid_skill_heals_and_exact_public_retry_replays(
    tmp_path: Path, backend: str, trained: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, 7, trained=trained)
    command = await begin(play, cid)
    result = await service(play).execute(cid, command, principal_id="a")
    selected = await play.store.read(cid)
    assert await service(play).execute(cid, command, principal_id="a") == result
    public = json.dumps(
        {"operation": "gurps-recovery", "command": command.model_dump(mode="json")}, sort_keys=True
    )
    assert await play.store.duplicate(cid, command.id, public) == selected
    with pytest.raises(ConflictError, match="different input"):
        await play.store.duplicate(cid, command.id, public + " ")
    task = play._load(selected).resources.recovery_tasks[0]
    assert task.skill == (7 if trained else 3) and minimum.enabled(
        play._load(selected).resources, task
    )
    await wait(play, cid, task.due - task.start)
    done = await service(play).execute(
        cid,
        FinishRecovery(
            id="finish", actor_id="a", expected_revision=await revision(play, cid), task_id="aid"
        ),
        principal_id="a",
    )
    assert done.check and done.check.effective_target == (7 if trained else 3)
    assert done.check.total == 3 and done.hp_recovered == 4
    await replay(play, cid, tmp_path / "seed-replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_pending_two_stays_legacy_when_finish_is_new(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await fixture(tmp_path, backend, 6)
    monkeypatch.setattr(generations, "ACTIVE", False)
    command = await begin(play, cid)
    await service(play).execute(cid, command, principal_id="a")
    saved = await play.store.command_input(cid, command.id)
    assert saved and saved.text and "medical_minimum_generation" not in saved.text
    monkeypatch.setattr(generations, "ACTIVE", True)
    await wait(play, cid, 600)
    done = await service(play).execute(
        cid,
        FinishRecovery(
            id="finish", actor_id="a", expected_revision=await revision(play, cid), task_id="aid"
        ),
        principal_id="a",
    )
    assert done.check and done.check.effective_target == 2 and done.check.total == 3
    assert done.hp_recovered == 4
    # The old producer never captured its external environment. Reexecute from
    # its genuine retained pending checkpoint; do not invent those missing facts.
    await replay(play, cid, tmp_path / "old-seed-replay", after="aid")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_new_pending_late_two_interrupts_without_discarding_prior_health_roll(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await fixture(tmp_path, backend)
    await service(play).execute(cid, await begin(play, cid), principal_id="a")
    play.seeds = lambda: f"{0:064x}"  # Actual HT9 total10 fails normally: tipsy, IQ-1.
    drink = await DrinkingService(play, lambda *_: DrinkingEnvironment()).execute(
        cid,
        DrinkCommand(
            id="drink",
            actor_id="a",
            expected_revision=await revision(play, cid),
            kind="drink",
            drinks=3,
        ),
        principal_id="a",
    )
    assert drink.level == "tipsy" and drink.check and drink.check.total == 10
    assert play._load(await play.store.read(cid)).resources.recovery_tasks[0].status == "pending"
    await wait(play, cid, 600)
    before = play._load(await play.store.read(cid))
    monkeypatch.setattr(generations, "ACTIVE", False)
    command = FinishRecovery(
        id="finish", actor_id="a", expected_revision=before.revision, task_id="aid"
    )

    def forbidden_draw(_self: SeededRandom, _bound: int) -> int:
        raise AssertionError("Unavailable treatment must not draw original or complication dice")

    with monkeypatch.context() as entropy:
        entropy.setattr(SeededRandom, "randbelow", forbidden_draw)
        done = await service(play).execute(cid, command, principal_id="a")
    assert done.status == "interrupted" and done.check is None and done.hp_recovered == 0
    after = play._load(await play.store.read(cid))
    assert after.resources.game_time == before.resources.game_time == 600
    assert (
        after.resources.pools == before.resources.pools
        and after.resources.intoxications == before.resources.intoxications
    )
    assert after.resources.recovery_tasks[0].settled
    assert await service(play).execute(cid, command, principal_id="a") == done
    await replay(play, cid, tmp_path / "interrupted-seed-replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_source_surgery_default_two_receives_real_tl7_plus_one_before_admission(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, surgical=True)
    # B223 Physician7-5 gives Surgery2; B424 TL7 surgical equipment gives +1.
    medical = MedicalService(play, lambda *_: CareEnvironment(technology_level=7))
    await medical.execute(
        cid,
        BeginRecovery(
            id="surgery",
            actor_id="a",
            expected_revision=await revision(play, cid),
            kind="repair-lasting",
            target_id="b",
            injury_id="leg",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    task = state.resources.recovery_tasks[0]
    assert task.skill == 2 and task.treatment_modifier == 1 and task.due - task.start == 7200
    await wait(play, cid, 7200)
    done = await medical.execute(
        cid,
        FinishRecovery(
            id="finish",
            actor_id="a",
            expected_revision=await revision(play, cid),
            task_id="surgery",
        ),
        principal_id="a",
    )
    assert (
        done.check and done.check.effective_target == 3 and done.check.total == 3 and done.repaired
    )
    hp = next(p for p in play._load(await play.store.read(cid)).resources.pools if p.id == "hp:b")
    assert hp.injury and hp.injury.lasting_injuries[0].recovery_at == 610320
    await replay(play, cid, tmp_path / "surgical-seed-replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_minimum_marker_is_private_and_cannot_be_authored_at_genesis(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    await service(play).execute(cid, await begin(play, cid), principal_id="a")
    state = play._load(await play.store.read(cid))
    runtime = build_runtime(play)
    assert minimum.PREFIX not in str(await runtime.read(cid, principal_id="a"))
    member = next(m for m in state.members if m.principal_id == "a")
    assert minimum.PREFIX not in str(project("stream", ViewRequest(runtime, state, member)))
    initial = (await play.store.history(cid))[0].state_after.copy()
    initial["revision"] = 0
    marker = next(e for e in state.resources.events if e.id.startswith(minimum.PREFIX))
    with pytest.raises(ValidationError, match="reserved|runtime|private|receipt|command"):
        play.initial_state(
            initial,
            state.world,
            state.resources.model_copy(
                update={
                    "revision": 0,
                    "events": (marker,),
                    "recovery_tasks": (),
                }
            ),
            (),
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_source_surgical_two_without_positive_facility_modifier_refuses_before_work(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, surgical=True)
    before = await play.store.read(cid)
    calls = []

    def environment(*_: object) -> CareEnvironment:
        calls.append("captured")
        return CareEnvironment(technology_level=6)  # B424 basic TL6 surgical equipment: +0.

    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="at least 3"):
        await MedicalService(play, environment).execute(
            cid,
            BeginRecovery(
                id="surgery",
                actor_id="a",
                expected_revision=await revision(play, cid),
                kind="repair-lasting",
                target_id="b",
                injury_id="leg",
            ),
            principal_id="a",
        )
    assert calls == ["captured"] and play.rng.exhausted()
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_mandatory_natural_ht_two_remains_legal_and_uses_its_existing_roll(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, ht=2)
    medical = MedicalService(play, lambda *_: CareEnvironment(food=True, water=True))
    await medical.execute(
        cid,
        BeginRecovery(
            id="natural",
            actor_id="a",
            target_id="a",
            expected_revision=await revision(play, cid),
            kind="natural",
        ),
        principal_id="a",
    )
    remaining = 86400
    index = 0
    while remaining:
        ticks = min(remaining, 10000)
        await play.execute(
            cid,
            Wait(
                id=f"wait-{index}",
                actor_id="a",
                ticks=ticks,
                expected_revision=await revision(play, cid),
            ),
            principal_id="a",
        )
        remaining -= ticks
        index += 1
    done = await medical.execute(
        cid,
        FinishRecovery(
            id="finish",
            actor_id="a",
            task_id="natural",
            expected_revision=await revision(play, cid),
        ),
        principal_id="a",
    )
    assert done.check and done.check.effective_target == 2 and done.check.total == 3
    assert done.hp_recovered == 1 and done.status == "completed"
    assert not any(
        e.id.startswith(minimum.PREFIX)
        for e in play._load(await play.store.read(cid)).resources.events
    )
