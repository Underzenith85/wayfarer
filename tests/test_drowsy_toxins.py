"""B428 awake drowsiness reaches real ordinary IQ and DX checks from exposure."""

import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, build_runtime, open_store, played, seed_campaign
from test_actions import campaign
from test_gurps_melee import setup
from test_toxins import state as toxin_state

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.rules.types.toxin import DeliveryEvidence, ToxinProfile
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, CheckRule, Inspect, Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.condition_generations import condition_generation
from wayfarer.engine.simulation.health.drowsiness import event_id
from wayfarer.engine.simulation.health.fatigue import ContinueExertion, FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.toxins import ToxinCommand, apply_toxin
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.drowsy_toxins import DrowsyExposure, DrowsyToxinService
from wayfarer.orchestration.physical_checks import (
    PhysicalCheck,
    PhysicalCheckCommand,
    PhysicalCheckService,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"


def exposure() -> DrowsyExposure:
    # Scenario-authored dose lasts one minute. B428 supplies its check penalty,
    # not this substance's duration. No two-hour inactive sleep check is due.
    return DrowsyExposure(
        profile=ToxinProfile(
            id="scenario-sedative",
            vector="digestive",
            condition="drowsy",
            condition_seconds=60,
            reference="B438-B440",
        ),
        evidence=DeliveryEvidence(swallowed=True),
    )


async def fixture(path: Path, backend: str) -> tuple[str, PlayService]:
    chemistry = RuleDefinition(
        "skill:chemistry",
        DefinitionKind.SKILL,
        "Chemistry",
        "sjg:basic-set-characters-4e-2004",
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.IQ, Difficulty.HARD, "B183"),
    )
    cid, play = await setup(
        path / "fixture",
        PROFILE,
        start_encounter=False,
        aware_of=("chest", "b"),
        extra_definitions=(chemistry,),
        extra_purchases=(Purchase(definition_id=chemistry.id, amount=4),),
        runtime_rules=ActionRules(
            id="drowsy-check",
            version=1,
            checks=(
                CheckRule(
                    id="examine-compound",
                    action="inspect",
                    target_id="chest",
                    definition_id=chemistry.id,
                    package_id="package:gurps-basic-set-4e-2004-characters",
                    package_version="1.0.0",
                    reveal_fact_ids=("clue",),
                ),
            ),
        ),
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    return cid, play


async def revision(play: PlayService, cid: str) -> int:
    return (await play.store.read(cid))["revision"]


async def produce(play: PlayService, cid: str) -> None:
    service = DrowsyToxinService(play, lambda *_: exposure())
    for kind in ("expose", "resolve"):
        await service.execute(
            cid,
            ToxinCommand(
                id=kind,
                actor_id="a",
                expected_revision=await revision(play, cid),
                kind=kind,
                exposure_id="dose",
            ),
            principal_id="gm",
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("expired", [False, True])
async def test_awake_drowsiness_changes_actual_iq_skill_and_disclosure(
    tmp_path: Path, backend: str, expired: bool
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((4, 4, 4))
    await produce(play, cid)
    if expired:
        await play.execute(
            cid,
            Wait(id="expiry", actor_id="a", expected_revision=await revision(play, cid), ticks=60),
            principal_id="a",
        )
    state = play._load(await play.store.read(cid))
    assert state.resources.toxins[0].condition_until == 60
    assert sum(m.value for m in check_modifiers(state.resources, "a", "iq")) == (
        0 if expired else -2
    )
    assert check_modifiers(state.resources, "a", "iq", defensive=True) == ()
    assert check_modifiers(state.resources, "a", "will", defensive=True) == ()
    assert check_modifiers(state.resources, "a", "per", defensive=True) == ()
    assert check_modifiers(state.resources, "a", "ht") == ()
    command = Inspect(
        id="chemistry",
        actor_id="a",
        target_id="chest",
        expected_revision=await revision(play, cid),
    )
    play.rng = RecordedDice((3, 3, 3))
    result = await play.execute(cid, command, principal_id="a")
    assert result.check and result.check.effective_target == (10 if expired else 8)
    assert result.check.total == 9 and result.check.outcome.succeeded == expired
    after = play._load(await play.store.read(cid))
    assert (("a", "clue") in after.world.knowledge) == expired
    assert after.actors == state.actors
    for actor in ("a", "b"):
        view = json.dumps(await build_runtime(play).read(cid, principal_id=actor))
        assert "toxin-drowsiness:" not in view and "scenario-sedative" not in view
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_dx_attack_uses_corrected_awake_penalty(tmp_path: Path, backend: str) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((4, 4, 4))
    await produce(play, cid)
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((4, 4, 4))
    result = await service.execute(
        cid,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.attack.effective_target == 11
    assert result.injury.attack.total == 12 and result.injury.injury == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("expired", [False, True])
async def test_iq_penalty_reaches_real_ordinary_perception_and_will_consumers(
    tmp_path: Path, backend: str, expired: bool
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((4, 4, 4))
    await produce(play, cid)
    if expired:
        await play.execute(
            cid,
            Wait(id="expiry", actor_id="a", expected_revision=await revision(play, cid), ticks=60),
            principal_id="a",
        )
    play.rng = RecordedDice((3, 3, 3))
    heard = await PhysicalCheckService(
        play, lambda *_: PhysicalCheck("sense", sense="hearing")
    ).execute(
        cid,
        PhysicalCheckCommand(
            id="hear",
            actor_id="a",
            expected_revision=await revision(play, cid),
            trigger_id="quiet-sound",
        ),
        principal_id="gm",
    )
    assert heard == expired
    resources = play._load(await play.store.read(cid)).resources
    hearing = next(e for e in resources.events if e.id.startswith("physical-check:"))
    assert json.loads(hearing.kind)["effective_target"] == (10 if expired else 8)
    # Canonical reducer diagnostic after an actual persisted toxin producer:
    # B426 ordinary fatigue reaches zero; continuation is a real Will check.
    depleted, _ = apply_fatigue(
        resources,
        FatigueCost(id="spent", actor_id="a", expected_revision=resources.revision, amount=10),
        ht=10,
        rng=RecordedDice(()),
        system=True,
    )
    continued, result = apply_fatigue(
        depleted,
        ContinueExertion(id="continue", actor_id="a", expected_revision=depleted.revision),
        ht=10,
        will=10,
        rng=RecordedDice((3, 3, 3)),
        system=True,
    )
    assert result.checks[0].effective_target == (10 if expired else 8)
    assert result.checks[0].total == 9 and result.allowed == expired
    fp = next(p for p in continued.pools if p.id == "fp:a")
    assert fp.fatigue and fp.fatigue.collapsed == (not expired)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_successful_toxin_resistance_prevents_drowsiness(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    play.rng = RecordedDice((3, 3, 3))
    await produce(play, cid)
    resources = play._load(await play.store.read(cid)).resources
    assert resources.toxins[0].condition_until == 0
    assert check_modifiers(resources, "a", "dx") == check_modifiers(resources, "a", "iq") == ()
    play.rng = RecordedDice((3, 3, 3))
    result = await play.execute(
        cid,
        Inspect(
            id="resisted-chemistry",
            actor_id="a",
            target_id="chest",
            expected_revision=await revision(play, cid),
        ),
        principal_id="a",
    )
    assert result.check and result.check.effective_target == 10 and result.check.outcome.succeeded


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_trusted_exposure_authority_rollback_retry_and_seeded_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    initial = await play.store.read(cid)
    service = DrowsyToxinService(play, lambda *_: exposure())
    command = ToxinCommand(
        id="dose",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="expose",
        exposure_id="dose",
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await service.execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command.model_copy(update={"expected_revision": 99}), principal_id="gm"
        )
    assert await play.store.read(cid) == before
    play.rng = secrets
    play.seeds = lambda: f"{3:064x}"
    result = await service.execute(cid, command, principal_id="gm")
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    changed = exposure().model_copy(
        update={"profile": exposure().profile.model_copy(update={"condition_seconds": 999})}
    )
    assert (
        await DrowsyToxinService(restarted, lambda *_: changed).execute(
            cid, command, principal_id="gm"
        )
        == result
    )
    play.rng = RecordedDice(())
    resolve = command.model_copy(
        update={"id": "resolve", "kind": "resolve", "expected_revision": await revision(play, cid)}
    )
    after_exposure = await play.store.read(cid)
    with pytest.raises(ValidationError, match="dice"):
        await service.execute(cid, resolve, principal_id="gm")
    assert await play.store.read(cid) == after_exposure
    play.rng = secrets
    resolved = await service.execute(cid, resolve, principal_id="gm")
    assert resolved.check and resolved.check.total == 11 and not resolved.resisted
    play.seeds = lambda: f"{1:064x}"
    checked = await play.execute(
        cid,
        Inspect(
            id="seeded-chemistry",
            actor_id="a",
            target_id="chest",
            expected_revision=await revision(play, cid),
        ),
        principal_id="a",
    )
    assert checked.check and checked.check.effective_target == 8 and checked.check.total == 9
    assert not checked.check.outcome.succeeded
    final = await play.store.read(cid)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final == await play.store.replay(cid)


def test_legacy_toxin_producer_retains_recorded_check_semantics() -> None:
    resources, _ = apply_toxin(
        toxin_state(),
        ToxinCommand(
            id="expose", actor_id="a", expected_revision=0, kind="expose", exposure_id="x"
        ),
        profile=exposure().profile,
        evidence=exposure().evidence,
        ht=10,
        rng=RecordedDice(()),
        system=True,
    )
    resources, _ = apply_toxin(
        resources,
        ToxinCommand(
            id="resolve", actor_id="a", expected_revision=1, kind="resolve", exposure_id="x"
        ),
        rng=RecordedDice((4, 4, 4)),
        system=True,
    )
    with condition_generation(False):
        assert check_modifiers(resources, "a", "dx") == ()
        assert sum(m.value for m in check_modifiers(resources, "a", "iq")) == -1
    assert sum(m.value for m in check_modifiers(resources, "a", "dx")) == -2
    assert sum(m.value for m in check_modifiers(resources, "a", "iq")) == -2


@pytest.mark.parametrize("unsupported", ["damage", "long-duration", "overlap", "delivery"])
async def test_bounded_toxin_context_refuses_without_writes(
    tmp_path: Path, unsupported: str
) -> None:
    cid, play = await fixture(tmp_path, "sqlite")
    context = exposure()
    if unsupported == "damage":
        context = context.model_copy(
            update={"profile": context.profile.model_copy(update={"hp_add": 1})}
        )
    elif unsupported == "long-duration":
        context = context.model_copy(
            update={"profile": context.profile.model_copy(update={"condition_seconds": 7200})}
        )
    elif unsupported == "delivery":
        context = context.model_copy(update={"evidence": DeliveryEvidence()})
    else:
        play.rng = RecordedDice((4, 4, 4))
        await produce(play, cid)
    before = await play.store.read(cid)
    command = ToxinCommand(
        id="unsupported",
        actor_id="a",
        expected_revision=before["revision"],
        kind="expose",
        exposure_id="new-dose",
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await DrowsyToxinService(play, lambda *_: context).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("mutation", ["2", "true", "{}", "actor", "duplicate"])
async def test_corrupt_recorded_drowsiness_generation_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    cid, play = await fixture(tmp_path, "sqlite")
    play.rng = RecordedDice((4, 4, 4))
    await produce(play, cid)
    resources = play._load(await play.store.read(cid)).resources
    marker = next(e for e in resources.events if e.id == event_id("dose"))
    changed = marker.model_copy(
        update={"target_id": "b"} if mutation == "actor" else {"kind": mutation}
    )
    events = tuple(changed if e.id == marker.id else e for e in resources.events)
    if mutation == "duplicate":
        events += (marker,)
    with pytest.raises(ValidationError, match="drowsiness generation"):
        check_modifiers(resources.model_copy(update={"events": events}), "a", "iq")


async def test_genesis_cannot_author_drowsiness_generation(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path, "sqlite")
    state = play._load(await play.store.read(cid))
    forged = state.resources.model_copy(
        update={"events": (ResourceEvent(id=event_id("dose"), at=0, target_id="a", kind="1"),)}
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            forged,
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
