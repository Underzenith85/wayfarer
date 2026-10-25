"""B376 automatic heavy bare Parry failure moves the actual Staff victim."""

import json
import secrets
from decimal import Decimal
from pathlib import Path

import pytest
from support.melee_spell import cast, fixture, revision
from support.runtime import build_play, build_runtime, played
from test_drowsy_toxins import exposure as drowsy_exposure
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.melee.heavy_bare_parry import HeavyBareParryResult
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.toxins import ToxinCommand
from wayfarer.engine.simulation.magic.melee_spell_state import MeleeSpellContactResult
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
    generations,
)
from wayfarer.orchestration.drowsy_toxins import DrowsyToxinService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def prepare(
    path: Path, backend: str, *, edge: bool = False, diagonal: bool = False, defender_st: int = 4
) -> tuple[str, PlayService, Campaign]:
    cid, play, initial = await fixture(path, backend, defender_st=defender_st)
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid, energy=1)
    await play.execute(
        cid,
        Wait(id="next-second", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight-start",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=8 if edge else 0, y=0), facing="east"),
                Placement(
                    actor_id="b",
                    position=GridPoint(x=9 if edge else 1, y=int(diagonal)),
                    facing="west",
                ),
            ),
        ),
        principal_id="gm",
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="real-staff",
            mode_id="staff-thrust",
            target_id="b",
        ),
        principal_id="a",
    )
    return cid, play, initial


def response(revision: int) -> ChooseDefense:
    return ChooseDefense(
        id="bare-parry",
        actor_id="b",
        expected_revision=revision,
        encounter_id="fight",
        defense="parry",
        item_id="left-hand",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("falls", [False, True])
async def test_heavy_parry_automatically_fails_then_moves_one_yard_and_checks_dx(
    tmp_path: Path, backend: str, falls: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, _ = await prepare(tmp_path, backend)
    command = response(await revision(play, cid))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="b",
        )
    assert await play.store.read(cid) == before
    # Attack9, physical1, magic1, and DX15/6. No Parry dice or B557 table.
    dice = (3, 3, 3, 1, 1) + ((5, 5, 5) if falls else (2, 2, 2))
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(dice))
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    with pytest.raises(RuntimeError, match="crash after candidate checkpoint"):
        await submit(
            failing,
            cid,
            CombatService(failing).plan(cid, command, features=generations.ACTIVE),
            principal_id="b",
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    recorded = RecordedDice(dice)
    play.rng = recorded
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert recorded.exhausted() and result.injury is not None
    assert result.injury.defense is None and result.injury.injury == 1
    assert result.injury.critical_table == () and result.injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    defender = next(p for p in state.encounters[0].participants if p.actor_id == "b")
    assert defender.position == GridPoint(x=2, y=0)
    assert (defender.posture == "prone") == falls
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.current == 2 and hp.injury is not None and hp.injury.prone == falls
    heavy = HeavyBareParryResult.model_validate_json(
        next(
            e.kind
            for e in state.resources.events
            if e.id.startswith("melee-spell:heavy-bare-parry:")
        )
    )
    assert heavy.incoming_weight == 4000 and heavy.basic_lift_millipounds == Decimal(3200)
    assert (
        heavy.automatic_failure
        and heavy.displacement.potential_yards == heavy.displacement.moved_yards == 1
    )
    assert heavy.displacement.fell == falls and heavy.displacement.adjudication_required is None
    assert len(heavy.displacement.checks) == 1
    assert heavy.displacement.checks[0].effective_target == 10
    magic = MeleeSpellContactResult.model_validate_json(
        next(e.kind for e in state.resources.events if e.id.startswith("melee-spell:result:"))
    )
    assert magic.triggered and magic.injury == 1 and magic.status == "spent"
    final = await play.store.read(cid)
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"staff-heavy-bare-parry"})
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seeded_manufactured_heavy_staff_parry_reexecutes_from_mundane_genesis(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await prepare(tmp_path, backend)
    play.rng = secrets
    await CombatService(play).execute(cid, response(await revision(play, cid)), principal_id="b")
    state = play._load(await play.store.read(cid))
    assert any(e.id.startswith("melee-spell:heavy-bare-parry:") for e in state.resources.events)
    private_event = next(
        e for e in state.resources.events if e.id.startswith("melee-spell:heavy-bare-parry:")
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            initial,
            state.world,
            state.resources.model_copy(update={"revision": 0, "events": (private_event,)}),
            (),
        )
    for route in ("campaign", "stream"):
        for principal in ("alice", "bob"):
            encoded = json.dumps(
                await build_runtime(play).project(route, cid, principal_id=principal)
            )
            assert "melee-spell:heavy-bare-parry:" not in encoded
            assert "basic_lift_millipounds" not in encoded
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
    assert json.loads(replayed["play_json"]) == json.loads(
        (await play.store.read(cid))["play_json"]
    )


@pytest.mark.parametrize(
    "edge,diagonal,legacy", [(True, False, False), (False, True, False), (False, False, True)]
)
async def test_unrepresented_geometry_and_legacy_generation_refuse_before_dice(
    tmp_path: Path, edge: bool, diagonal: bool, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, _ = await prepare(tmp_path, "sqlite", edge=edge, diagonal=diagonal)
    if legacy:
        monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"staff-heavy-bare-parry"})
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice(())
    with pytest.raises(
        ValidationError,
        match="knockback destination|diagonal knockback|heavy barehanded|Selected defense",
    ):
        await CombatService(play).execute(
            cid, response(await revision(play, cid)), principal_id="b"
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


async def test_below_lift_bare_parry_keeps_ordinary_defense_roll_and_contact_arc(
    tmp_path: Path,
) -> None:
    cid, play, _ = await prepare(tmp_path, "sqlite", defender_st=5)
    # BL5lb exceeds the4lb staff. A real Parry6 succeeds; only magic arcs through.
    dice = RecordedDice((3, 3, 3, 2, 2, 2, 1))
    play.rng = dice
    result = await CombatService(play).execute(
        cid, response(await revision(play, cid)), principal_id="b"
    )
    assert dice.exhausted() and result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.outcome.succeeded and result.injury.injury == 0
    state = play._load(await play.store.read(cid))
    assert next(
        p for p in state.encounters[0].participants if p.actor_id == "b"
    ).position == GridPoint(x=1, y=0)
    assert not any(e.id.startswith("melee-spell:heavy-bare-parry:") for e in state.resources.events)


@pytest.mark.parametrize("critical", [False, True])
async def test_missed_or_critical_attack_has_no_bare_parry_attempt_knockback(
    tmp_path: Path, critical: bool
) -> None:
    cid, play, _ = await prepare(tmp_path, "sqlite")
    dice = RecordedDice((1, 1, 1, 3, 3, 3, 1, 1) if critical else (5, 5, 5))
    play.rng = dice
    result = await CombatService(play).execute(
        cid, response(await revision(play, cid)), principal_id="b"
    )
    assert dice.exhausted() and result.injury is not None and result.injury.defense is None
    assert result.injury.injury == int(critical)
    state = play._load(await play.store.read(cid))
    assert next(
        p for p in state.encounters[0].participants if p.actor_id == "b"
    ).position == GridPoint(x=1, y=0)
    assert not any(e.id.startswith("melee-spell:heavy-bare-parry:") for e in state.resources.events)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b421_exempts_actual_drowsy_defender_fall_reaction(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await prepare(tmp_path, backend)
    service = DrowsyToxinService(play, lambda *_: drowsy_exposure())
    # Actual swallowed, non-damaging scenario sedative and failed HT12 against10.
    play.rng = RecordedDice((4, 4, 4))
    for kind in ("expose", "resolve"):
        await service.execute(
            cid,
            ToxinCommand(
                id="defender-dose-" + kind,
                actor_id="b",
                expected_revision=await revision(play, cid),
                kind=kind,
                exposure_id="defender-dose",
            ),
            principal_id="gm",
        )
    state = play._load(await play.store.read(cid))
    assert sum(m.value for m in check_modifiers(state.resources, "b", "dx")) == -2
    assert check_modifiers(state.resources, "b", "dx", defensive=True) == ()
    # DX9 stays upright against unpenalized DX10, rather than falling against8.
    dice = RecordedDice((3, 3, 3, 1, 1, 3, 3, 3))
    play.rng = dice
    result = await CombatService(play).execute(cid, response(state.revision), principal_id="b")
    assert dice.exhausted() and result.injury is not None and result.injury.injury == 1
    after = play._load(await play.store.read(cid))
    heavy = HeavyBareParryResult.model_validate_json(
        next(
            e.kind
            for e in after.resources.events
            if e.id.startswith("melee-spell:heavy-bare-parry:")
        )
    )
    trace = heavy.displacement.checks[0]
    assert trace.base_target == trace.effective_target == 10 and trace.modifiers == ()
    assert trace.total == 9 and trace.outcome.succeeded and not heavy.displacement.fell
    defender = next(p for p in after.encounters[0].participants if p.actor_id == "b")
    assert defender.position == GridPoint(x=2, y=0) and defender.posture == "standing"
    assert await play.store.read(cid) == await play.store.replay(cid)
