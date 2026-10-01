"""Independent B130/B161 host, exact-clock and durable concurrency evidence."""

import asyncio
import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_play
from test_actions import campaign, world
from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm
from test_physiology_traits import compiler
from test_statistics import gurps_draft, profile_package
from trait_support import options

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.physiology import PROFILE, package
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.transformations import TransformationRules
from wayfarer.engine.simulation.events import ResourceChanged, visible
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
from wayfarer.engine.simulation.traits.harmful_physiology_state import (
    AdvancePhysiology,
    DeclarePhysiologyCalendar,
    HarmfulCondition,
    ObservePhysiology,
    ReconcilePhysiology,
    SettlePhysiology,
    conditions,
)
from wayfarer.engine.simulation.traits.physiology import history as injury_history
from wayfarer.engine.simulation.traits.physiology_calendar import PhysiologyCalendar
from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.advancement import AdvanceCharacter, AdvancementService, GrantPoints
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


def trait(source: str = "weakness", frequency: str = "minute") -> Purchase:
    return Purchase(
        definition_id="disadvantage:" + source,
        trait=options(rarity="common", interval=frequency),
    )


async def prepare(
    path: Path,
    backend: str = "sqlite",
    *,
    source: str = "weakness",
    frequency: str = "minute",
    transformations: TransformationRules | None = None,
    pending_other: bool = False,
) -> tuple[str, PlayService]:
    compiled = compiler()
    base = profile_package(PROFILE)
    rules_package = replace(
        base, id="package:test-physiology", definitions=base.definitions + package().definitions
    )
    resources = ResourceEngine(
        world(), RulesCatalog((rules_package,)), compiled.rules, compiled.policy, ()
    )
    engine = ActionEngine(
        PowerReviewer(
            compiled,
            PowerPolicy(id="physiology-test", version=1, automatic_approval=not pending_other),
            frozenset({"gm"}),
        ),
        resources,
        ActionRules(
            id="physiology-test", version=1, maximum_wait=10000, transformations=transformations
        ),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    await seed_play(
        play,
        initial,
        world(),
        ResourceState(
            owners=tuple(
                Owner(actor_id=actor, capacity=100)
                for actor in (("a", "b") if pending_other else ("a",))
            ),
            pools=(
                Pool(id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            ),
        ),
        tuple(
            ActorSetup(
                actor_id=actor,
                proposal=CharacterProposal(draft=gurps_draft(trait(source, frequency))),
                body=HumanBody(anatomy="human"),
            )
            for actor in (("a", "b") if pending_other else ("a",))
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    return initial["id"], play


def observation(
    revision: int = 0,
    *,
    identifier: str = "exposure",
    present: bool = True,
    source: str = "weakness",
    contact: bool = False,
) -> ObservePhysiology:
    return ObservePhysiology(
        id=identifier,
        actor_id="a",
        expected_revision=revision,
        source="weakness" if source == "weakness" else "dependency",
        condition_id="sunlight" if source == "weakness" else "medicine",
        present=present,
        dependency_mode="contact" if contact else "dose",
    )


async def condition(play: PlayService, cid: str) -> HarmfulCondition:
    return conditions(play._load(await play.store.read(cid)).resources)[0]


async def clock(play: PlayService, cid: str, to: int, *, identifier: str = "clock") -> None:
    revision = (await play.store.read(cid))["revision"]
    ticks = to - play._load(await play.store.read(cid)).resources.game_time
    if ticks > 10000:
        await HarmfulPhysiologyService(play).execute(
            cid,
            AdvancePhysiology(id=identifier, actor_id="a", expected_revision=revision, to=to),
            principal_id="gm",
        )
    else:
        result = await play.execute(
            cid,
            Wait(id=identifier, actor_id="a", expected_revision=revision, ticks=ticks),
            principal_id="a",
        )
        assert result.status == "committed", result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_exposure_drives_canonical_injury_single_consumption_and_private_replay(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    first = await condition(play, cid)
    assert first.due == 60
    await clock(play, cid, 59)
    with pytest.raises(ConflictError, match="harmful physiology"):
        await clock(play, cid, 61, identifier="too-far")
    await clock(play, cid, 60, identifier="at-deadline")
    before = await play.store.read(cid)
    settle = SettlePhysiology(
        id="settle", actor_id="a", expected_revision=3, interval_id=first.interval_id
    )
    with pytest.raises(ConflictError, match="owed"):
        await service.execute(
            cid, observation(3, identifier="hide", present=False), principal_id="gm"
        )
    with pytest.raises(ConflictError, match="owed"):
        await service.execute(cid, observation(3, identifier="renew"), principal_id="gm")
    assert await play.store.read(cid) == before
    play.rng = RecordedDice((6, 4, 4, 4))
    other = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice((6, 4, 4, 4)))
    outcomes = await asyncio.gather(
        service.execute(cid, settle, principal_id="gm"),
        HarmfulPhysiologyService(other).execute(cid, settle, principal_id="gm"),
    )
    assert outcomes[0] == outcomes[1]
    result = outcomes[0].injuries[0]
    assert result.damage_dice == (6,) and result.hp_before == 10 and result.hp_after == 4
    assert result.injury is not None and result.injury.checks[0].reason == "major-wound"
    saved = await play.store.read(cid)
    state = play._load(saved)
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.stunned and hp.injury.prone and hp.injury.shock == 4
    assert state.revision == state.resources.revision == 4
    assert (await condition(play, cid)).due == 120
    assert len(injury_history(state.resources)) == 1
    with pytest.raises(ConflictError, match="consumed"):
        await service.execute(
            cid,
            settle.model_copy(update={"id": "alias", "expected_revision": 4}),
            principal_id="gm",
        )
    with pytest.raises(ConflictError):
        await service.execute(
            cid, settle.model_copy(update={"interval_id": "altered"}), principal_id="gm"
        )
    with pytest.raises(ConflictError):
        await service.execute(
            cid, observation(3, identifier="stale", present=False), principal_id="gm"
        )
    for principal in ("alice", "outsider"):
        with pytest.raises((ValidationError, NotFoundError)):
            await service.execute(cid, settle, principal_id=principal)
    stream = await play.store.stream(cid)
    private = [
        e.event
        for e in stream
        if isinstance(e.event, ResourceChanged)
        and e.event.fact.id.startswith("harmful-physiology:")
    ]
    assert private and all(
        visible(e, CampaignMember(principal_id="gm", role="gm")) for e in private
    )
    assert all(
        not visible(e, CampaignMember(principal_id="alice", role="player", actor_ids=("a",)))
        for e in private
    )
    runtime = build_runtime(play)
    view = json.dumps(await runtime.read(cid, principal_id="alice"))
    events = await runtime.events(cid, principal_id="alice")
    for field in ("harmful-physiology:", "purchase_digest", "damage_dice", "sunlight"):
        assert field not in view and all(field not in e.model_dump_json() for e in events)
    restart = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await restart.store.read(cid) == saved == await restart.store.replay(cid)
    assert (
        await HarmfulPhysiologyService(restart).execute(cid, settle, principal_id="gm")
        == outcomes[0]
    )
    await HarmfulPhysiologyService(restart).execute(
        cid, observation(4, identifier="leave", present=False), principal_id="gm"
    )
    assert (await condition(restart, cid)).due is None
    await clock(restart, cid, 180, identifier="safe")
    assert (
        next(
            p.current
            for p in restart._load(await restart.store.read(cid)).resources.pools
            if p.id == "hp:a"
        )
        == 4
    )
    # Old exact retries retain their historical injury, even after the exposure ended.
    assert (
        await HarmfulPhysiologyService(restart).execute(cid, settle, principal_id="gm")
        == outcomes[0]
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seeded_scheduler_crosses_each_deadline_once_and_reexecutes(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, source="dependency", frequency="hour")
    initial = await play.store.read(cid)
    play.rng = secrets
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(source="dependency"), principal_id="gm")
    # B130: a missed hourly dose causes 1 HP per ten minutes, beginning at 70 minutes.
    assert (await condition(play, cid)).due == 4200
    command = AdvancePhysiology(id="scheduler", actor_id="a", expected_revision=1, to=4800)
    result = await service.execute(cid, command, principal_id="gm")
    assert [(i.hp_before, i.hp_after) for i in result.injuries] == [(10, 9), (9, 8)]
    assert (await condition(play, cid)).due == 5400
    assert (await play.store.read(cid))["revision"] == 2
    records = await played(play.store, cid)
    replayed, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)
    restart = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert (
        await HarmfulPhysiologyService(restart).execute(cid, command, principal_id="gm") == result
    )
    await HarmfulPhysiologyService(restart).execute(
        cid, observation(2, identifier="dose", source="dependency"), principal_id="gm"
    )
    assert (await condition(restart, cid)).due == 9000
    await clock(restart, cid, 8999)
    assert len(injury_history(restart._load(await restart.store.read(cid)).resources)) == 2


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_dependency_contact_requires_full_duration_and_cannot_erase_due_harm(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, source="dependency", frequency="day")
    service = HarmfulPhysiologyService(play)
    await service.execute(
        cid, observation(source="dependency", present=False, contact=True), principal_id="gm"
    )
    # Declared currently missing daily rest: B130 requires a full hour in the coffin.
    assert (await condition(play, cid)).due == 3600
    await clock(play, cid, 100)
    await service.execute(
        cid,
        observation(2, identifier="enter", source="dependency", contact=True),
        principal_id="gm",
    )
    assert (await condition(play, cid)).contact_due == 3700
    await clock(play, cid, 200, identifier="short-contact")
    await service.execute(
        cid,
        observation(4, identifier="leave", source="dependency", present=False, contact=True),
        principal_id="gm",
    )
    assert (await condition(play, cid)).due == 3600 and (
        await condition(play, cid)
    ).contact_due is None
    assert (await condition(play, cid)).contact_seconds == 100
    await clock(play, cid, 300, identifier="return-clock")
    await service.execute(
        cid,
        observation(6, identifier="return", source="dependency", contact=True),
        principal_id="gm",
    )
    await clock(play, cid, 3600, identifier="owed")
    with pytest.raises(ConflictError, match="owed"):
        await service.execute(
            cid,
            observation(
                8, identifier="leave-at-due", source="dependency", present=False, contact=True
            ),
            principal_id="gm",
        )
    result = await service.execute(
        cid,
        AdvancePhysiology(id="complete", actor_id="a", expected_revision=8, to=3800),
        principal_id="gm",
    )
    assert [(i.hp_before, i.hp_after) for i in result.injuries] == [(10, 9)]
    current = await condition(play, cid)
    assert current.present and current.due is None and current.contact_due is None
    assert current.contact_seconds == 0
    await clock(play, cid, 5000, identifier="leave-clock")
    await service.execute(
        cid,
        observation(
            10, identifier="leave-after-full", source="dependency", present=False, contact=True
        ),
        principal_id="gm",
    )
    assert (await condition(play, cid)).due == 5000 + 86400 + 3600


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_calendar_is_persisted_and_used_for_monthly_deadlines(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, source="dependency", frequency="month")
    service = HarmfulPhysiologyService(play)
    with pytest.raises(ValidationError, match="calendar"):
        await service.execute(cid, observation(source="dependency"), principal_id="gm")
    cal = PhysiologyCalendar(month_boundaries=(0, 31 * 86400, 59 * 86400, 90 * 86400))
    await service.execute(
        cid,
        DeclarePhysiologyCalendar(id="calendar", actor_id="gm", expected_revision=0, calendar=cal),
        principal_id="gm",
    )
    await clock(play, cid, 30 * 86400)
    await service.execute(cid, observation(2, source="dependency"), principal_id="gm")
    # Jan 31 clamps to Feb 28; B130's first missing-month injury is one day later.
    assert (await condition(play, cid)).due == 59 * 86400
    restart = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    result = await HarmfulPhysiologyService(restart).execute(
        cid,
        AdvancePhysiology(id="month", actor_id="a", expected_revision=3, to=59 * 86400),
        principal_id="gm",
    )
    assert result.injuries[0].hp_after == 9
    assert (await condition(restart, cid)).due == 60 * 86400


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_dependency_death_retires_future_injury(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend, source="dependency")
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(source="dependency", present=False), principal_id="gm")
    play.rng = RecordedDice((6, 6, 6))
    result = await service.execute(
        cid,
        AdvancePhysiology(id="fatal", actor_id="a", expected_revision=1, to=1260),
        principal_id="gm",
    )
    assert len(result.injuries) == 20 and result.injuries[-1].hp_after == -10
    hp = next(p for p in play._load(await play.store.read(cid)).resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.dead
    assert (await condition(play, cid)).retired and (await condition(play, cid)).due is None


@pytest.mark.parametrize("retry", [False, True])
async def test_authority_is_current_at_commit_and_duplicate(tmp_path: Path, retry: bool) -> None:
    cid, original = await prepare(tmp_path)
    if retry:
        await HarmfulPhysiologyService(original).execute(cid, observation(), principal_id="gm")
    revision = int(retry)
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    play = build_play(tmp_path, original.engine, store=store, rng=RecordedDice(()))
    store.revoke = lambda: revoke_gm(original, cid, revision)
    with pytest.raises(ValidationError, match="director authority"):
        await HarmfulPhysiologyService(play).execute(
            cid, observation() if retry else observation(1), principal_id="gm"
        )
    assert (await original.store.read(cid))["revision"] == revision + 1


async def replace_purchase(play: PlayService, cid: str, frequency: str | None) -> None:
    service = AdvancementService(play)
    state = play._load(await play.store.read(cid))
    await service.grant(
        cid,
        GrantPoints(
            id="grant",
            actor_id="gm",
            target_actor_id="a",
            expected_revision=state.revision,
            points=100,
            reason="buy off weakness",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert state.actors[0].approval is not None
    await service.advance(
        cid,
        AdvanceCharacter(
            id="replace",
            actor_id="a",
            expected_revision=state.revision,
            expected_build_revision=state.actors[0].approval.build_revision,
            draft=gurps_draft(*((trait(frequency=frequency),) if frequency else ())),
            reason="approved replacement",
        ),
        principal_id="a",
    )


@pytest.mark.parametrize("frequency,expected", [("five-minutes", 330), (None, None)])
async def test_current_approved_build_changes_future_eligibility(
    tmp_path: Path, frequency: str | None, expected: int | None
) -> None:
    cid, play = await prepare(tmp_path)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    await clock(play, cid, 30)
    await replace_purchase(play, cid, frequency)
    await service.execute(
        cid,
        ReconcilePhysiology(id="refresh", actor_id="a", expected_revision=4, source="weakness"),
        principal_id="gm",
    )
    assert (await condition(play, cid)).due == expected
    await clock(play, cid, 300, identifier="past-old-deadline")
    assert len(injury_history(play._load(await play.store.read(cid)).resources)) == 0


async def test_changed_purchase_cannot_erase_an_already_owed_interval(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    await clock(play, cid, 60)
    with pytest.raises(ConflictError, match="harmful physiology"):
        await replace_purchase(play, cid, None)
    state = play._load(await play.store.read(cid))
    assert state.revision == 3  # The point grant committed; the build change did not.
    assert any(
        p.definition_id == "disadvantage:weakness" for p in state.actors[0].proposal.draft.purchases
    )
    assert (await condition(play, cid)).due == 60
    play.rng = RecordedDice((2,))
    await service.execute(
        cid,
        SettlePhysiology(
            id="settle-first",
            actor_id="a",
            expected_revision=3,
            interval_id=(await condition(play, cid)).interval_id,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert state.actors[0].approval is not None
    await AdvancementService(play).advance(
        cid,
        AdvanceCharacter(
            id="buy-off",
            actor_id="a",
            expected_revision=4,
            expected_build_revision=state.actors[0].approval.build_revision,
            draft=gurps_draft(),
            reason="buy off settled weakness",
        ),
        principal_id="a",
    )
    await service.execute(
        cid,
        AdvancePhysiology(id="safe", actor_id="a", expected_revision=5, to=180),
        principal_id="gm",
    )
    assert (await condition(play, cid)).retired
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "hp:a"
        )
        == 8
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_different_command_ids_race_for_one_persisted_interval(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await HarmfulPhysiologyService(play).execute(cid, observation(), principal_id="gm")
    await clock(play, cid, 60)
    deadline = await condition(play, cid)
    first = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice((1,)))
    second = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice((1,)))
    commands = [
        SettlePhysiology(
            id=identifier, actor_id="a", expected_revision=2, interval_id=deadline.interval_id
        )
        for identifier in ("one", "alias")
    ]
    results = await asyncio.gather(
        HarmfulPhysiologyService(first).execute(cid, commands[0], principal_id="gm"),
        HarmfulPhysiologyService(second).execute(cid, commands[1], principal_id="gm"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    state = play._load(await play.store.read(cid))
    assert state.revision == 3 and len(injury_history(state.resources)) == 1
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 9
    assert (await condition(play, cid)).due == 120


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_random_weakness_scheduler_reexecution_uses_recorded_seed(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    play.rng = secrets
    initial = await play.store.read(cid)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    result = await service.execute(
        cid,
        AdvancePhysiology(id="two-rolls", actor_id="a", expected_revision=1, to=120),
        principal_id="gm",
    )
    assert len(result.injuries) == 2
    assert all(
        len(i.damage_dice) == 1 and i.hp_before - i.hp_after == sum(i.damage_dice)
        for i in result.injuries
    )
    _, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-replay"),
    )
    assert all(c.folded and c.reexecuted for c in checks)


@pytest.mark.parametrize(
    "source,frequency,first,cadence",
    [
        ("weakness", "minute", 60, 60),
        ("weakness", "five-minutes", 300, 300),
        ("weakness", "thirty-minutes", 1800, 1800),
        ("dependency", "minute", 60, 60),
        ("dependency", "hour", 4200, 600),
        ("dependency", "day", 90000, 3600),
        ("dependency", "week", 626400, 21600),
        ("dependency", "month", 32 * 86400, 86400),
        ("dependency", "season", 94 * 86400, 3 * 86400),
        ("dependency", "year", 380 * 86400, 14 * 86400),
    ],
)
async def test_source_frequencies_drive_real_host_deadlines(
    tmp_path: Path, source: str, frequency: str, first: int, cadence: int
) -> None:
    cid, play = await prepare(tmp_path, source=source, frequency=frequency)
    service = HarmfulPhysiologyService(play)
    revision = 0
    if frequency in ("month", "season", "year"):
        # Gregorian leap-year 2024, with February 2025 available for year clamping.
        lengths = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31, 31, 28)
        boundaries = [0]
        for days in lengths:
            boundaries.append(boundaries[-1] + days * 86400)
        await service.execute(
            cid,
            DeclarePhysiologyCalendar(
                id="calendar",
                actor_id="gm",
                expected_revision=0,
                calendar=PhysiologyCalendar(month_boundaries=tuple(boundaries)),
            ),
            principal_id="gm",
        )
        revision = 1
    await service.execute(
        cid,
        observation(
            revision, source=source, present=not (source == "dependency" and frequency == "minute")
        ),
        principal_id="gm",
    )
    assert (await condition(play, cid)).due == first
    play.rng = RecordedDice((2, 2)) if source == "weakness" else RecordedDice(())
    result = await service.execute(
        cid,
        AdvancePhysiology(
            id="due", actor_id="a", expected_revision=revision + 1, to=first + cadence
        ),
        principal_id="gm",
    )
    loss = 2 if source == "weakness" else 1
    assert [(i.hp_before, i.hp_after) for i in result.injuries] == [
        (10, 10 - loss),
        (10 - loss, 10 - 2 * loss),
    ]
    assert (await condition(play, cid)).due == first + cadence * 2


async def test_early_settlement_identity_alias_and_payload_forgery_fail_without_changes(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    before = await play.store.read(cid)
    pending = await condition(play, cid)
    with pytest.raises(ConflictError, match="exact persisted deadline"):
        await service.execute(
            cid,
            SettlePhysiology(
                id="early", actor_id="a", expected_revision=1, interval_id=pending.interval_id
            ),
            principal_id="gm",
        )
    with pytest.raises(ConflictError, match="aliased"):
        await service.execute(
            cid,
            observation(1, identifier="alias").model_copy(
                update={"condition_id": "another-sunlight"}
            ),
            principal_id="gm",
        )
    value = observation(1, identifier="injected").model_dump()
    value["amount"] = 100
    value["started"] = 500
    with pytest.raises(ValidationError, match="Invalid harmful"):
        await service.execute(cid, value, principal_id="gm")
    assert await play.store.read(cid) == before


async def test_continuous_dependency_supply_suspends_damage_until_removed(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, source="dependency")
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(source="dependency"), principal_id="gm")
    assert (await condition(play, cid)).due is None
    await clock(play, cid, 180)
    await service.execute(
        cid,
        observation(2, source="dependency", identifier="remove", present=False),
        principal_id="gm",
    )
    assert (await condition(play, cid)).due == 240
    await service.execute(
        cid,
        AdvancePhysiology(id="injure", actor_id="a", expected_revision=3, to=240),
        principal_id="gm",
    )
    await service.execute(
        cid, observation(4, source="dependency", identifier="restore"), principal_id="gm"
    )
    assert (await condition(play, cid)).due is None
    await clock(play, cid, 600, identifier="safe")
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "hp:a"
        )
        == 9
    )


async def test_initial_brief_contact_does_not_create_a_satisfied_grace_period(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path, source="dependency", frequency="day")
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(source="dependency", contact=True), principal_id="gm")
    await clock(play, cid, 30)
    await service.execute(
        cid,
        observation(2, identifier="stop", source="dependency", contact=True, present=False),
        principal_id="gm",
    )
    current = await condition(play, cid)
    assert current.contact_seconds == 30 and current.due == 3600 and current.contact_due is None
    result = await service.execute(
        cid,
        AdvancePhysiology(id="missing", actor_id="a", expected_revision=3, to=3600),
        principal_id="gm",
    )
    assert result.injuries[0].hp_after == 9


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_scheduler_clock_injury_and_private_facts_rollback_together(
    tmp_path: Path, backend: str
) -> None:
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    from wayfarer.orchestration.pipeline import submit

    cid, play = await prepare(tmp_path, backend, source="dependency")
    await HarmfulPhysiologyService(play).execute(
        cid, observation(source="dependency", present=False), principal_id="gm"
    )
    before = await play.store.read(cid)
    records = await played(play.store, cid)
    stream = await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    service = HarmfulPhysiologyService(failing)
    command = AdvancePhysiology(id="atomic", actor_id="a", expected_revision=1, to=120)
    with pytest.raises(RuntimeError, match="crash after candidate checkpoint"):
        await submit(
            failing,
            cid,
            service.plan(failing, failing._load(before), command, principal_id="gm"),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await played(play.store, cid) == records and await play.store.stream(cid) == stream
    result = await HarmfulPhysiologyService(play).execute(cid, command, principal_id="gm")
    assert len(result.injuries) == 2 and result.injuries[-1].hp_after == 8


async def test_transformation_admission_boundary_leaves_timed_expiry_executable(
    tmp_path: Path,
) -> None:
    """The unsupported combination is rejected before it can strand a timed reversion."""
    from test_transformations import attachment_routes

    from wayfarer.engine.simulation.campaign.transformations import TraitRoute, TransformationRule
    from wayfarer.orchestration.transformations import TransformationService

    original = gurps_draft(trait(frequency="five-minutes"))
    rule = TransformationRule(
        id="temporary-frequency",
        actor_id="a",
        kind="supernatural-affliction",
        source_ref="B296",
        target=gurps_draft(trait()),
        target_body_id="a:temporary",
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body") for p in original.purchases
        ),
        attachment_routes=attachment_routes(),
        point_policy="adjust",
        voluntary=False,
        expires_after_seconds=60,
        curable=True,
    )
    cid, play = await prepare(
        tmp_path,
        frequency="five-minutes",
        transformations=TransformationRules(
            id="physiology-forms", version=1, transformations=(rule,)
        ),
    )
    transform = TransformationService(play)
    state = play._load(await play.store.read(cid))
    assert state.actors[0].approval is not None
    proposed = await transform.execute(
        cid,
        {
            "operation": "propose",
            "id": "propose",
            "actor_id": "a",
            "expected_revision": 0,
            "expected_build_revision": state.actors[0].approval.build_revision,
            "rule_id": rule.id,
        },
        principal_id="alice",
    )
    active = await transform.execute(
        cid,
        {
            "operation": "approve",
            "id": "approve",
            "actor_id": "a",
            "expected_revision": 1,
            "proposal_id": proposed.proposal_id,
            "reason": "temporary body frequency",
        },
        principal_id="gm",
    )
    assert active.status == "active" and active.expires_at == 60
    service = HarmfulPhysiologyService(play)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="transformations require supported ordering"):
        await service.execute(cid, observation(2), principal_id="gm")
    assert await play.store.read(cid) == before
    assert conditions(play._load(before).resources) == ()
    await clock(play, cid, 60)
    await transform.execute(
        cid,
        {
            "operation": "resolve",
            "id": "expire",
            "actor_id": "a",
            "expected_revision": 3,
            "proposal_id": proposed.proposal_id,
            "resolution": "expire",
        },
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    replacement = next(
        p
        for p in state.actors[0].proposal.draft.purchases
        if p.definition_id == "disadvantage:weakness"
    )
    assert replacement.trait is not None
    assert dict(replacement.trait.parameters)["interval"] == "five-minutes"
    assert state.transformations.records[-1].status == "reverted"
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authored_transform_admission_and_migration_refuse_atomically(
    tmp_path: Path, backend: str
) -> None:
    from test_transformations import attachment_routes

    from wayfarer.engine.simulation.campaign.transformations import TraitRoute, TransformationRule
    from wayfarer.orchestration.advancement import ApplyMigration, MigrationService

    target_draft = gurps_draft(trait(frequency="five-minutes"))
    rule = TransformationRule(
        id="body",
        actor_id="a",
        kind="body-modification",
        source_ref="B294",
        target=target_draft,
        target_body_id="a:other",
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body")
            for p in target_draft.purchases
        ),
        attachment_routes=attachment_routes(),
        point_policy="adjust",
    )
    rules = TransformationRules(id="forms", version=1, transformations=(rule,))
    configured_cid, configured = await prepare(
        tmp_path / "configured", backend, transformations=rules
    )
    before = await configured.store.read(configured_cid)
    records = await played(configured.store, configured_cid)
    stream = await configured.store.stream(configured_cid)
    with pytest.raises(ValidationError, match="transformations require supported ordering"):
        await HarmfulPhysiologyService(configured).execute(
            configured_cid, observation(), principal_id="gm"
        )
    assert await configured.store.read(configured_cid) == before
    assert await played(configured.store, configured_cid) == records
    assert await configured.store.stream(configured_cid) == stream
    cid, play = await prepare(tmp_path / "ordinary", backend)
    await HarmfulPhysiologyService(play).execute(cid, observation(), principal_id="gm")
    target_engine = ActionEngine(
        play.engine.reviewer,
        play.engine.resources,
        play.engine.rules.model_copy(update={"transformations": rules}),
    )
    target = PlayService(play.store, target_engine, rng=RecordedDice(()))
    before = await play.store.read(cid)
    stream = await play.store.stream(cid)
    with pytest.raises(ValidationError, match="transformations require supported ordering"):
        await MigrationService(play, target).apply(
            cid,
            ApplyMigration(
                id="migration",
                actor_id="gm",
                expected_revision=1,
                expected_from_digest=play.engine.digest,
                reason="add body forms",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.stream(cid) == stream
    assert (await condition(play, cid)).due == 60
    play.rng = RecordedDice((2,))
    result = await HarmfulPhysiologyService(play).execute(
        cid,
        AdvancePhysiology(id="still-works", actor_id="a", expected_revision=1, to=60),
        principal_id="gm",
    )
    assert result.injuries[0].hp_after == 8
    # A retired binding may migrate, but cannot sneak back through either
    # reconciliation entry point after an approved re-purchase.
    retired_cid, retired = await prepare(tmp_path / "retired", backend)
    await HarmfulPhysiologyService(retired).execute(retired_cid, observation(), principal_id="gm")
    await replace_purchase(retired, retired_cid, None)
    await HarmfulPhysiologyService(retired).execute(
        retired_cid,
        ReconcilePhysiology(id="retire", actor_id="a", expected_revision=3, source="weakness"),
        principal_id="gm",
    )
    replacement_engine = ActionEngine(
        retired.engine.reviewer,
        retired.engine.resources,
        retired.engine.rules.model_copy(update={"transformations": rules}),
    )
    migrated = PlayService(retired.store, replacement_engine, rng=RecordedDice(()))
    await MigrationService(retired, migrated).apply(
        retired_cid,
        ApplyMigration(
            id="allow-migration",
            actor_id="gm",
            expected_revision=4,
            expected_from_digest=retired.engine.digest,
            reason="no active harmful physiology",
        ),
        principal_id="gm",
    )
    state = migrated._load(await migrated.store.read(retired_cid))
    assert state.actors[0].approval is not None
    await AdvancementService(migrated).advance(
        retired_cid,
        AdvanceCharacter(
            id="repurchase",
            actor_id="a",
            expected_revision=5,
            expected_build_revision=state.actors[0].approval.build_revision,
            draft=gurps_draft(trait(frequency="five-minutes"), st_level=12),
            reason="balanced approved purchase",
        ),
        principal_id="a",
    )
    before = await migrated.store.read(retired_cid)
    for command in (
        ReconcilePhysiology(id="reactivate", actor_id="a", expected_revision=6, source="weakness"),
        AdvancePhysiology(id="reactivate-clock", actor_id="a", expected_revision=6, to=300),
    ):
        with pytest.raises(ValidationError, match="transformations require supported ordering"):
            await HarmfulPhysiologyService(migrated).execute(
                retired_cid, command, principal_id="gm"
            )
        assert await migrated.store.read(retired_cid) == before
    assert (await condition(migrated, retired_cid)).retired


@pytest.mark.parametrize(
    "source,modifier",
    [("dependency", "aging"), ("weakness", "fatigue-only"), ("weakness", "variable")],
)
def test_unreviewed_special_variants_cannot_enter_an_approved_build(
    source: str, modifier: str
) -> None:
    from wayfarer.engine.rules.traits.base import TraitOptions

    result = compiler().compile(
        gurps_draft(
            Purchase(
                definition_id="disadvantage:" + source,
                trait=TraitOptions(
                    parameters=(("rarity", "common"), ("interval", "minute")), modifiers=(modifier,)
                ),
            )
        )
    )
    assert result.build is None and result.diagnostics


async def test_calendar_extension_keeps_pending_obligations_and_three_month_seasons(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path, source="dependency", frequency="month")
    service = HarmfulPhysiologyService(play)
    cal = PhysiologyCalendar(month_boundaries=(0, 31 * 86400, 59 * 86400, 90 * 86400))
    await service.execute(
        cid,
        DeclarePhysiologyCalendar(id="calendar", actor_id="gm", expected_revision=0, calendar=cal),
        principal_id="gm",
    )
    await service.execute(cid, observation(1, source="dependency"), principal_id="gm")
    before = await play.store.read(cid)
    changed = PhysiologyCalendar(month_boundaries=(0, 30 * 86400, 59 * 86400, 90 * 86400))
    with pytest.raises(ConflictError, match="extend"):
        await service.execute(
            cid,
            DeclarePhysiologyCalendar(
                id="rewrite", actor_id="gm", expected_revision=2, calendar=changed
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    extended = cal.model_copy(update={"month_boundaries": cal.month_boundaries + (120 * 86400,)})
    await service.execute(
        cid,
        DeclarePhysiologyCalendar(
            id="extend", actor_id="gm", expected_revision=2, calendar=extended
        ),
        principal_id="gm",
    )
    assert (await condition(play, cid)).due == 32 * 86400
    seasonal_cid, seasonal = await prepare(
        tmp_path / "seasonal", source="dependency", frequency="season"
    )
    seasonal_service = HarmfulPhysiologyService(seasonal)
    await seasonal_service.execute(
        seasonal_cid,
        DeclarePhysiologyCalendar(
            id="calendar",
            actor_id="gm",
            expected_revision=0,
            calendar=extended.model_copy(update={"months_per_season": 4}),
        ),
        principal_id="gm",
    )
    before = await seasonal.store.read(seasonal_cid)
    with pytest.raises(ValidationError, match="three months"):
        await seasonal_service.execute(
            seasonal_cid, observation(1, source="dependency"), principal_id="gm"
        )
    assert await seasonal.store.read(seasonal_cid) == before


async def test_repurchase_restores_persisted_exposure_with_a_new_full_interval(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    await replace_purchase(play, cid, None)
    await service.execute(
        cid,
        ReconcilePhysiology(id="retire", actor_id="a", expected_revision=3, source="weakness"),
        principal_id="gm",
    )
    await clock(play, cid, 30)
    state = play._load(await play.store.read(cid))
    assert state.actors[0].approval is not None
    await AdvancementService(play).advance(
        cid,
        AdvanceCharacter(
            id="restore",
            actor_id="a",
            expected_revision=5,
            expected_build_revision=state.actors[0].approval.build_revision,
            draft=gurps_draft(trait(), st_level=14),
            reason="balanced approved re-purchase",
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((2,))
    result = await service.execute(
        cid,
        AdvancePhysiology(id="new-interval", actor_id="a", expected_revision=6, to=90),
        principal_id="gm",
    )
    assert (
        len(result.injuries) == 1
        and result.injuries[0].hp_before == 14
        and result.injuries[0].hp_after == 12
    )
    assert (await condition(play, cid)).due == 150 and (await condition(play, cid)).present


async def test_completed_contact_credit_cannot_be_reused_next_cycle(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, source="dependency", frequency="hour")
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(source="dependency", contact=True), principal_id="gm")
    await clock(play, cid, 300)
    await service.execute(
        cid,
        observation(2, identifier="pause", source="dependency", contact=True, present=False),
        principal_id="gm",
    )
    assert (await condition(play, cid)).contact_seconds == 300
    await clock(play, cid, 400, identifier="resume-clock")
    await service.execute(
        cid,
        observation(4, identifier="resume", source="dependency", contact=True),
        principal_id="gm",
    )
    await service.execute(
        cid,
        AdvancePhysiology(id="first-full", actor_id="a", expected_revision=5, to=700),
        principal_id="gm",
    )
    current = await condition(play, cid)
    assert current.contact_seconds == 0 and current.contact_due is None and current.due is None
    # Ongoing satisfying presence is safe; leaving cannot retain the old partial credit.
    await service.execute(
        cid,
        observation(6, identifier="leave", source="dependency", contact=True, present=False),
        principal_id="gm",
    )
    await clock(play, cid, 800, identifier="return-clock")
    await service.execute(
        cid,
        observation(8, identifier="return", source="dependency", contact=True),
        principal_id="gm",
    )
    assert (await condition(play, cid)).contact_due == 1400
    await service.execute(
        cid,
        AdvancePhysiology(id="second-full", actor_id="a", expected_revision=9, to=1400),
        principal_id="gm",
    )
    assert (await condition(play, cid)).contact_seconds == 0
    await clock(play, cid, 1500, identifier="stay")
    await service.execute(
        cid,
        observation(11, identifier="leave-again", source="dependency", contact=True, present=False),
        principal_id="gm",
    )
    assert (await condition(play, cid)).due == 5700


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("operation", ["settle", "reconcile", "advance"])
async def test_other_injury_death_retires_past_deadline_without_current_approval(
    tmp_path: Path, backend: str, operation: str
) -> None:
    from wayfarer.contracts import Campaign, CommandReceipt
    from wayfarer.engine.simulation.health.injury import Wound, apply_injury
    from wayfarer.engine.simulation.resources import Advance

    cid, play = await prepare(tmp_path, backend)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    pending = await condition(play, cid)

    def fatal(campaign: Campaign) -> CommandReceipt:
        before = play._load(campaign)
        resources, result = apply_injury(
            before.resources,
            Wound(
                id="other-injury",
                actor_id="a",
                expected_revision=1,
                basic_damage=60,
                resistance=0,
                damage_type="tox",
                injury_source="internal",
            ),
            ht=10,
            rng=RecordedDice(()),
            system=True,
        )
        play.commit(
            campaign,
            before.model_copy(
                update={
                    "revision": resources.revision,
                    "resources": resources,
                    "actors": tuple(a.model_copy(update={"approval": None}) for a in before.actors),
                }
            ),
        )
        return CommandReceipt(action="resource", outcome=result.model_dump_json())

    await play.store.commit_turn(
        cid, "other-injury", 1, "canonical fatal injury", fatal, actor_id="gm"
    )

    def generic_clock(campaign: Campaign) -> CommandReceipt:
        before = play._load(campaign)
        resources = play.engine.resources.apply(
            before.resources,
            Advance(id="generic-clock", actor_id="b", expected_revision=2, to=180),
            system=True,
            rng=RecordedDice(()),
        )
        play.commit(
            campaign,
            before.model_copy(update={"revision": resources.revision, "resources": resources}),
        )
        return CommandReceipt(action="resource", outcome="clock")

    await play.store.commit_turn(
        cid, "generic-clock", 2, "generic clock after death", generic_clock, actor_id="gm"
    )
    assert (await condition(play, cid)).due == 60
    commands = {
        "settle": SettlePhysiology(
            id="retire", actor_id="a", expected_revision=3, interval_id=pending.interval_id
        ),
        "reconcile": ReconcilePhysiology(
            id="retire", actor_id="a", expected_revision=3, source="weakness"
        ),
        "advance": AdvancePhysiology(id="retire", actor_id="b", expected_revision=3, to=240),
    }
    result = await service.execute(cid, commands[operation], principal_id="gm")
    assert not result.injuries and (await condition(play, cid)).retired
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == (240 if operation == "advance" else 180)
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.current == -50 and hp.injury is not None and hp.injury.dead
    assert state.actors[0].approval is None
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Dead actors"):
        await service.execute(cid, observation(4, identifier="dead-exposure"), principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unrelated_unapproved_actor_does_not_block_subject_or_calendar(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.orchestration.play import ApproveCharacter

    cid, play = await prepare(tmp_path, backend, pending_other=True)
    service = HarmfulPhysiologyService(play)
    await service.execute(
        cid,
        DeclarePhysiologyCalendar(
            id="calendar",
            actor_id="gm",
            expected_revision=0,
            calendar=PhysiologyCalendar(month_boundaries=(0, 31 * 86400, 59 * 86400)),
        ),
        principal_id="gm",
    )
    await play.approve(
        cid,
        ApproveCharacter(
            id="approve-a",
            actor_id="gm",
            target_actor_id="a",
            expected_revision=1,
            reason="approve only this actor",
        ),
        principal_id="gm",
    )
    await service.execute(cid, observation(2), principal_id="gm")
    play.rng = RecordedDice((2,))
    result = await service.execute(
        cid,
        AdvancePhysiology(id="one-actor", actor_id="a", expected_revision=3, to=60),
        principal_id="gm",
    )
    assert result.injuries[0].hp_after == 8
    state = play._load(await play.store.read(cid))
    assert next(a for a in state.actors if a.actor_id == "b").approval is None
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10


async def test_unapproved_retired_binding_does_not_block_the_host_clock(tmp_path: Path) -> None:
    from wayfarer.contracts import Campaign, CommandReceipt

    cid, play = await prepare(tmp_path)
    service = HarmfulPhysiologyService(play)
    await service.execute(cid, observation(), principal_id="gm")
    await replace_purchase(play, cid, None)
    await service.execute(
        cid,
        ReconcilePhysiology(id="retire", actor_id="a", expected_revision=3, source="weakness"),
        principal_id="gm",
    )

    def revoke(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        play.commit(
            campaign,
            state.model_copy(
                update={
                    "revision": 5,
                    "resources": state.resources.model_copy(update={"revision": 5}),
                    "actors": tuple(a.model_copy(update={"approval": None}) for a in state.actors),
                }
            ),
        )
        return CommandReceipt(action="power-approval", outcome="revoked")

    await play.store.commit_turn(cid, "revoke", 4, "revoke approval", revoke, actor_id="gm")
    result = await service.execute(
        cid,
        AdvancePhysiology(id="clock", actor_id="b", expected_revision=5, to=100),
        principal_id="gm",
    )
    assert result.game_time == 100 and result.injuries == ()
    assert (await condition(play, cid)).retired
