"""Registered paid hand casting followed by actual punch/defense settlement."""

from pathlib import Path
from typing import Literal

import pytest
from support.hand_deathtouch import fixture, revision
from support.runtime import build_play

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.hand_melee_contact_state import contact_results
from wayfarer.engine.simulation.magic.hand_melee_spell_state import CastHandDeathtouch, casts
from wayfarer.engine.simulation.magic.melee_spell_state import HandCarrier
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.hand_melee_spells import HandMeleeSpellService
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, PrepareOwnerDamage
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.orchestration.unarmed_damage_records import (
    ArmedParryDamagePending,
    UnarmedDamagePending,
)


class NoDice:
    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        raise AssertionError("Paused contact requested a fresh random draw")


async def prepare(
    tmp_path: Path,
    backend: str,
    *,
    defender: Literal["unarmed", "staff", "armor", "knife"] = "unarmed",
) -> tuple[str, PlayService, CombatService]:
    cid, play, _ = await fixture(tmp_path, backend, defender=defender)
    hand_service = HandMeleeSpellService(play)
    play.rng = RecordedDice((3, 3, 3))
    for operation in ("start", "concentrate", "complete"):
        await hand_service.execute(
            cid,
            CastHandDeathtouch(
                id="hand-" + operation,
                actor_id="a",
                expected_revision=await revision(play, cid),
                operation=operation,
                cast_id="hand",
                energy=3,
                carrier=HandCarrier(hand="right-hand"),
            ),
            principal_id="alice",
        )
    assert play.rng.exhausted()
    charged = play._load(await play.store.read(cid))
    assert casts(charged.resources)["hand"].paid_fp == 2
    assert next(p.current for p in charged.resources.pools if p.id == "fp:a") == 8
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", expected_revision=charged.revision, ticks=1),
        principal_id="a",
    )
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="encounter",
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
    await combat.execute(
        cid,
        TakeUnarmedTurn(
            id="punch",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            action="punch",
            target_id="b",
            hands=("right-hand",),
            skill="skill:brawling",
            enter_close_combat=True,
        ),
        principal_id="a",
    )
    return cid, play, combat


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("defense", ["none", "dodge", "parry"])
async def test_paid_hand_charge_actual_punch_defense_and_exact_retry(
    tmp_path: Path, backend: str, defense: Literal["none", "dodge", "parry"]
) -> None:
    cid, play, combat = await prepare(tmp_path, backend)
    play.rng = RecordedDice(
        (2, 3, 3)
        + (() if defense == "none" else (2, 2, 2))
        + ((3,) if defense == "none" else ())
        + (() if defense == "dodge" else (1, 1, 1))
    )
    response = ChooseDefense(
        id="response",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense=defense,
        item_id="left-hand" if defense == "parry" else None,
    )
    accepted = await combat.execute(cid, response, principal_id="b")
    assert play.rng.exhausted()
    final = play._load(await play.store.read(cid))
    result = contact_results(final.resources)[0]
    assert result.triggered == (defense != "dodge")
    assert result.dice == (() if defense == "dodge" else (1, 1, 1))
    assert result.injury == (0 if defense == "dodge" else 3)
    assert casts(final.resources)["hand"].status == ("held" if defense == "dodge" else "spent")
    assert next(p.current for p in final.resources.pools if p.id == "hp:b") == (
        10 if defense == "dodge" else 6 if defense == "none" else 7
    )
    play.rng = RecordedDice(())
    assert await combat.execute(cid, response, principal_id="b") == accepted
    assert play._load(await play.store.read(cid)) == final
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == accepted
    assert restarted._load(await restarted.store.read(cid)) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("row,physical", [(3, 3), (5, 2), (6, 4)])
async def test_physical_critical_packet_does_not_multiply_hand_spell(
    tmp_path: Path, backend: str, row: int, physical: int
) -> None:
    cid, play, combat = await prepare(tmp_path, backend)
    table = {3: (1, 1, 1), 5: (1, 1, 3), 6: (2, 2, 2)}[row]
    play.rng = RecordedDice((1, 1, 1) + table + (() if row == 6 else (3,)) + (1, 1, 1))
    await combat.execute(
        cid,
        ChooseDefense(
            id="critical-response",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    trace = state.encounters[0].unarmed_history[-1]
    result = contact_results(state.resources)[0]
    assert trace.basic_damage == trace.injury == physical
    assert result.dice == (1, 1, 1) and result.injury == 3
    assert result.hp_before == 10 - physical and result.hp_after == 7 - physical


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_contextual_unarmed_miss_keeps_captured_pending_without_second_roll(
    tmp_path: Path, backend: str
) -> None:
    cid, play, combat = await prepare(tmp_path, backend)
    play.rng = RecordedDice((6, 6, 6, 1, 1, 1))
    response = ChooseDefense(
        id="paused-response",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="none",
    )
    accepted = await combat.execute(cid, response, principal_id="b")
    assert play.rng.exhausted()
    paused = play._load(await play.store.read(cid))
    encounter = paused.encounters[0]
    assert encounter.blocked_reason is not None and encounter.pending_unarmed is not None
    assert casts(paused.resources)["hand"].status == "held"
    assert contact_results(paused.resources)[0].outcome == "held"
    play.rng = RecordedDice(())
    assert await combat.execute(cid, response, principal_id="b") == accepted
    play.rng = NoDice()
    with pytest.raises(ConflictError, match="blocked"):
        await combat.execute(
            cid,
            response.model_copy(
                update={"id": "buy-another-roll", "expected_revision": paused.revision}
            ),
            principal_id="b",
        )
    assert play._load(await play.store.read(cid)) == paused


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_weapon_parry_counterinjury_retains_hand_charge(
    tmp_path: Path, backend: str
) -> None:
    cid, play, combat = await prepare(tmp_path, backend, defender="knife")
    play.rng = RecordedDice((2, 3, 3, 2, 2, 2, 2, 3, 3, 1))
    await combat.execute(
        cid,
        ChooseDefense(
            id="weapon-parry",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="parry",
            item_id="defender-implement",
            parry_mode_id="knife-swing",
        ),
        principal_id="b",
    )
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    result = contact_results(state.resources)[0]
    assert not result.triggered and result.dice == () and result.outcome == "held"
    assert casts(state.resources)["hand"].status == "held"
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 9


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("defense", ["dodge", "parry"])
async def test_failed_actual_defense_discharges_after_physical_hit(
    tmp_path: Path, backend: str, defense: Literal["dodge", "parry"]
) -> None:
    cid, play, combat = await prepare(tmp_path, backend)
    play.rng = RecordedDice((2, 3, 3, 5, 5, 5, 3, 1, 1, 1))
    await combat.execute(
        cid,
        ChooseDefense(
            id="failed-defense",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense=defense,
            item_id="left-hand" if defense == "parry" else None,
        ),
        principal_id="b",
    )
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    result = contact_results(state.resources)[0]
    assert result.ordinary_hit and result.triggered and result.injury == 3
    assert result.hp_before == 9 and result.hp_after == 6


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ordinary_miss_preserves_charge_without_defense_or_damage_draw(
    tmp_path: Path, backend: str
) -> None:
    cid, play, combat = await prepare(tmp_path, backend)
    play.rng = RecordedDice((5, 5, 5))
    await combat.execute(
        cid,
        ChooseDefense(
            id="miss-response",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="dodge",
        ),
        principal_id="b",
    )
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    result = contact_results(state.resources)[0]
    assert result.outcome == "held" and result.defense_check is None and result.dice == ()
    assert casts(state.resources)["hand"].status == "held"
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("success", [True, False])
async def test_contextual_bare_parry_distinguishes_determinate_arc_from_unresolved_hit(
    tmp_path: Path, backend: str, success: bool
) -> None:
    cid, play, combat = await prepare(tmp_path, backend)
    play.rng = RecordedDice(
        (2, 3, 3)
        + ((1, 1, 1) if success else (6, 6, 6))
        + (1, 1, 1)
        + ((1, 1, 1) if success else ())
    )
    response = ChooseDefense(
        id="bare-critical",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="parry",
        item_id="left-hand",
    )
    receipt = await combat.execute(cid, response, principal_id="b")
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert encounter.blocked_reason and encounter.pending_unarmed is not None
    results = contact_results(state.resources)
    assert len(results) == int(success)
    if success:
        assert results[0].triggered and results[0].injury == 3
    assert casts(state.resources)["hand"].status == ("spent" if success else "held")
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        7 if success else 10
    )
    play.rng = RecordedDice(())
    assert await combat.execute(cid, response, principal_id="b") == receipt
    assert play._load(await play.store.read(cid)) == state
    play.rng = NoDice()
    with pytest.raises(ConflictError, match="blocked"):
        await combat.execute(
            cid,
            response.model_copy(
                update={"id": "another-bare-roll", "expected_revision": state.revision}
            ),
            principal_id="b",
        )
    assert play._load(await play.store.read(cid)) == state


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_magical_dice_failure_rolls_back_physical_injury_then_retry_commits_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play, combat = await prepare(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="atomic-contact",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="none",
    )
    play.rng = RecordedDice((2, 3, 3, 3))
    with pytest.raises(ValidationError, match="more dice"):
        await combat.execute(cid, command, principal_id="b")
    assert play._load(await play.store.read(cid)) == before
    assert casts(before.resources)["hand"].status == "held"
    play.rng = RecordedDice((2, 3, 3, 3, 1, 1, 1))
    await combat.execute(cid, command, principal_id="b")
    assert play.rng.exhausted()
    after = play._load(await play.store.read(cid))
    assert len(contact_results(after.resources)) == 1
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 6


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("weapon", [False, True])
async def test_actual_typed_damage_stage_preserves_hand_contact_and_settles_once(
    tmp_path: Path, backend: str, weapon: bool
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, defender="knife" if weapon else "unarmed")
    state = play._load(await play.store.read(cid))
    response = ChooseDefense(
        id="stage-response",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="parry" if weapon else "none",
        item_id="defender-implement" if weapon else None,
        parry_mode_id="knife-swing" if weapon else None,
    )
    command = PrepareOwnerDamage(
        id=response.id,
        actor_id=response.actor_id,
        expected_revision=response.expected_revision,
        response=response,
    )
    play.rng = RecordedDice((2, 3, 3) + ((2, 2, 2, 2, 3, 3, 1) if weapon else (3,)))
    tasks = TaskService(play)
    await tasks.execute(cid, command, principal_id="bob")
    assert play.rng.exhausted()
    staged = play._load(await play.store.read(cid))
    pending = snapshot(staged).pending
    assert isinstance(pending, ArmedParryDamagePending if weapon else UnarmedDamagePending)
    assert pending is not None
    assert next(p.current for p in staged.resources.pools if p.id == "hp:b") == 10
    results = contact_results(staged.resources)
    assert len(results) == int(weapon)
    if weapon:
        assert results[0].outcome == "held" and not results[0].triggered
    owner = "b" if weapon else "a"
    choice = ChooseOwnerDamage(
        id="accept-damage",
        actor_id=owner,
        expected_revision=staged.revision,
        pending_id=pending.id,
        choice="accept",
    )
    play.rng = RecordedDice(() if weapon else (1, 1, 1))
    receipt = await tasks.execute(cid, choice, principal_id="bob" if weapon else "alice")
    assert play.rng.exhausted()
    final = play._load(await play.store.read(cid))
    assert snapshot(final).pending is None and final.encounters[0].pending_unarmed is None
    assert len(contact_results(final.resources)) == 1
    assert next(p.current for p in final.resources.pools if p.id == "hp:b") == (10 if weapon else 6)
    assert next(p.current for p in final.resources.pools if p.id == "hp:a") == (9 if weapon else 10)
    assert await tasks.execute(cid, choice, principal_id="bob" if weapon else "alice") == receipt
    assert play._load(await play.store.read(cid)) == final
