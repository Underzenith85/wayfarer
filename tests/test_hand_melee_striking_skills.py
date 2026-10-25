"""B182/B203 physical striking and B240/B245 held hand spell composition."""

import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError as SchemaError
from support.hand_deathtouch import cast, fixture, revision
from support.runtime import build_play, build_runtime, played
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.hand_melee_contact_state import (
    HandContact,
    contact_results,
    read_contact,
)
from wayfarer.engine.simulation.magic.hand_melee_spell_state import casts
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeUnarmedTurn,
    generations,
)
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, PrepareOwnerDamage
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.orchestration.unarmed_damage_records import UnarmedDamagePending
from wayfarer.persistence.replay import verify_commands

Skill = Literal["skill:boxing", "skill:karate"]
FEATURE = "hand-melee-striking-skills"


async def ready(
    path: Path,
    backend: str,
    skill: Skill,
    *,
    defender: Literal["unarmed", "armor", "knife", "shield"] = "unarmed",
    points: int = 8,
    purchased: bool = True,
    altered: bool = False,
    loaded: bool = False,
    seeded: bool = False,
) -> tuple[str, PlayService, Campaign]:
    cid, play, initial = await fixture(
        path,
        backend,
        defender=defender,
        striking_skill=skill,
        striking_points=points,
        purchase_striking=purchased,
        altered_striking=altered,
        caster_load=loaded,
        spectator=True,
    )
    play.rng = secrets if seeded else RecordedDice((3, 3, 3))
    await cast(play, cid)
    state = play._load(await play.store.read(cid))
    assert casts(state.resources)["hand"].status == "held"
    if not seeded:
        assert casts(state.resources)["hand"].paid_fp == 2
        assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 8
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", expected_revision=state.revision, ticks=1),
        principal_id="a",
    )
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    return cid, play, initial


def punch(revision: int, skill: Skill) -> TakeUnarmedTurn:
    return TakeUnarmedTurn(
        id="punch",
        actor_id="a",
        expected_revision=revision,
        encounter_id="fight",
        action="punch",
        target_id="b",
        skill=skill,
        hands=("right-hand",),
        enter_close_combat=True,
    )


async def declare(play: PlayService, cid: str, skill: Skill) -> HandContact:
    await CombatService(play).execute(
        cid, punch(await revision(play, cid), skill), principal_id="a"
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_unarmed
    assert pending is not None
    contact = read_contact(state.resources, pending.id)
    assert contact is not None and contact.striking_generation == 1
    return contact


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill", ["skill:boxing", "skill:karate"])
@pytest.mark.parametrize("armor", [False, True])
async def test_paid_hand_spell_uses_real_striking_skill_and_distinct_damage_packets(
    tmp_path: Path, backend: str, skill: Skill, armor: bool
) -> None:
    cid, play, _ = await ready(tmp_path, backend, skill, defender="armor" if armor else "unarmed")
    contact = await declare(play, cid, skill)
    response = ChooseDefense(
        id="response",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="none",
    )
    before, history = await play.store.read(cid), await play.store.history(cid)
    with pytest.raises(ValidationError, match="authorized"):
        await CombatService(play).execute(cid, response, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            response.model_copy(update={"expected_revision": response.expected_revision - 1}),
            principal_id="b",
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    # DX10 Boxing12 / Karate11 gives+2 per physical die. Punch is thrust−1.
    dice = (2, 3, 3, 3, 1, 1, 1)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(dice))
    with pytest.raises(RuntimeError, match="crash after candidate checkpoint"):
        await submit(
            failing,
            cid,
            CombatService(failing).plan(cid, response, features=generations.ACTIVE),
            principal_id="b",
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    recorded = RecordedDice(dice)
    play.rng = recorded
    result = await CombatService(play).execute(cid, response, principal_id="b")
    assert recorded.exhausted() and result.unarmed is not None
    assert result.unarmed.checks[0] is not None
    assert result.unarmed.checks[0].effective_target == (12 if skill == "skill:boxing" else 11)
    assert result.unarmed.injury == (0 if armor else 2)
    after = play._load(await play.store.read(cid))
    magical = contact_results(after.resources)[0]
    assert magical.pending_id == contact.pending_id and magical.triggered and magical.injury == 3
    assert magical.dice == (1, 1, 1) and magical.status == "spent"
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == (7 if armor else 5)
    assert casts(after.resources)["hand"].status == "spent"
    final = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill", ["skill:boxing", "skill:karate"])
@pytest.mark.parametrize("defense", ["dodge", "bare", "weapon"])
async def test_real_defense_implement_controls_hand_charge_retention_or_arc(
    tmp_path: Path, backend: str, skill: Skill, defense: str
) -> None:
    cid, play, _ = await ready(
        tmp_path, backend, skill, defender="knife" if defense == "weapon" else "unarmed"
    )
    await declare(play, cid, skill)
    # Successful knifeParry: its normal skill check and separate counterinjury die.
    dice = RecordedDice(
        (2, 3, 3, 2, 2, 2)
        + ((2, 3, 3, 1) if defense == "weapon" else (1, 1, 1) if defense == "bare" else ())
    )
    play.rng = dice
    await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="defense",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="dodge" if defense == "dodge" else "parry",
            item_id="left-hand"
            if defense == "bare"
            else "defender-implement"
            if defense == "weapon"
            else None,
            parry_mode_id="knife-swing" if defense == "weapon" else None,
        ),
        principal_id="b",
    )
    assert dice.exhausted()
    state = play._load(await play.store.read(cid))
    magical = contact_results(state.resources)[0]
    assert magical.triggered == (defense == "bare") and magical.injury == (
        3 if defense == "bare" else 0
    )
    assert casts(state.resources)["hand"].status == ("spent" if defense == "bare" else "held")
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        7 if defense == "bare" else 10
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("skill", ["skill:boxing", "skill:karate"])
@pytest.mark.parametrize("problem", ["unpurchased", "altered", "legacy", "kick"])
async def test_unsupported_or_unauthorized_striking_binding_refuses_before_dice(
    tmp_path: Path, skill: Skill, problem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, _ = await ready(
        tmp_path, "sqlite", skill, purchased=problem != "unpurchased", altered=problem == "altered"
    )
    if problem == "legacy":
        monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {FEATURE})
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice(())
    command = punch(await revision(play, cid), skill)
    if problem == "kick":
        command = command.model_copy(update={"action": "kick"})
    with pytest.raises(ConflictError, match="punch|purchased|canonical"):
        await CombatService(play).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill", ["skill:boxing", "skill:karate"])
async def test_captured_contact_survives_feature_absence_and_original_genesis_seed_replay(
    tmp_path: Path, backend: str, skill: Skill, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, initial = await ready(tmp_path, backend, skill, seeded=True)
    contact = await declare(play, cid, skill)
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {FEATURE})
    play.seeds = lambda: f"{19:064x}"
    await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="defense",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert contact_results(state.resources)[0].triggered
    final = await play.store.read(cid)
    for route in ("campaign", "stream"):
        for principal in ("alice", "bob", "watcher"):
            encoded = json.dumps(
                await build_runtime(play).project(route, cid, principal_id=principal)
            )
            assert "striking_generation" not in encoded and "charge_digest" not in encoded
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert json.loads(replayed["play_json"]) == json.loads(final["play_json"])
    for marker in (True, False, 0, 2):
        raw = contact.model_dump(mode="json")
        raw["striking_generation"] = marker
        with pytest.raises(SchemaError, match="exact integer"):
            HandContact.model_validate(raw)


@pytest.mark.parametrize("skill", ["skill:boxing", "skill:karate"])
async def test_karate_encumbrance_and_boxing_skill_keep_their_canonical_consumers(
    tmp_path: Path, skill: Skill
) -> None:
    cid, play, _ = await ready(tmp_path, "sqlite", skill, loaded=True)
    await declare(play, cid, skill)
    play.rng = RecordedDice((2, 3, 3, 3, 1, 1, 1))
    result = await CombatService(play).execute(
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
    assert result.unarmed is not None and result.unarmed.checks[0] is not None
    assert result.unarmed.checks[0].effective_target == (12 if skill == "skill:boxing" else 10)
    assert result.unarmed.injury == 2


async def test_existing_close_combat_shield_block_stays_unavailable(tmp_path: Path) -> None:
    cid, play, _ = await ready(tmp_path, "sqlite", "skill:karate", defender="shield")
    await declare(play, cid, "skill:karate")
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="defense|Dodge"):
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="block",
                actor_id="b",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                defense="block",
                item_id="defender-implement",
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_typed_owner_damage_keeps_new_striking_contact_and_settles_magic_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await ready(tmp_path, backend, "skill:karate")
    await declare(play, cid, "skill:karate")
    response = ChooseDefense(
        id="damage-stage",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="none",
    )
    play.rng = RecordedDice((2, 3, 3, 3))
    tasks = TaskService(play)
    await tasks.execute(
        cid,
        PrepareOwnerDamage(
            id=response.id,
            actor_id="b",
            expected_revision=response.expected_revision,
            response=response,
        ),
        principal_id="bob",
    )
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, UnarmedDamagePending)
    assert not contact_results(state.resources)
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    command = ChooseOwnerDamage(
        id="accept",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="accept",
    )
    dice = RecordedDice((1, 1, 1))
    play.rng = dice
    receipt = await tasks.execute(cid, command, principal_id="alice")
    assert dice.exhausted()
    final = await play.store.read(cid)
    after = play._load(final)
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 5
    assert (
        len(contact_results(after.resources)) == 1
        and casts(after.resources)["hand"].status == "spent"
    )
    assert await tasks.execute(cid, command, principal_id="alice") == receipt
    assert await play.store.read(cid) == final == await play.store.replay(cid)


async def test_generation_one_brawling_history_keeps_absent_striking_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_hand_deathtouch_host import prepare_contact

    active = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", active - {FEATURE})
    cid, play, initial = await fixture(tmp_path, "sqlite")
    await prepare_contact(play, cid, energy=1)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_unarmed
    assert pending is not None
    contact = read_contact(state.resources, pending.id)
    assert contact is not None and contact.striking_generation is None
    assert "striking_generation" not in contact.model_dump_json()
    monkeypatch.setattr(generations, "ACTIVE", active)
    play.seeds = lambda: f"{19:064x}"
    await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="defense",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    final = await play.store.read(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert json.loads(replayed["play_json"]) == json.loads(final["play_json"])
