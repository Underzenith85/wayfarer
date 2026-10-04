"""Actual registered empty-hand charge/punch protocol and saved-source boundaries."""

import json
from pathlib import Path
from typing import Literal

import pytest
from support.hand_deathtouch import cast, fixture, revision
from support.runtime import build_play, build_runtime

from wayfarer import validation
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.hand_melee_contact_state import contact_results, read_contact
from wayfarer.engine.simulation.magic.hand_melee_contacts import finish_contact
from wayfarer.engine.simulation.magic.hand_melee_spell_state import casts
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, StartEncounter
from wayfarer.orchestration.play import PlayService


async def charged(tmp_path: Path, backend: str) -> tuple[str, PlayService, Campaign]:
    cid, play, initial = await fixture(tmp_path, backend, spectator=True)
    play = build_play(tmp_path / "cast", play.engine, store=play.store, rng=RecordedDice((3, 3, 3)))
    await cast(play, cid, energy=1)
    await play.execute(
        cid,
        Wait(id="ready", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
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
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    return cid, play, initial


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("defense", ["none", "dodge", "parry"])
async def test_actual_hand_pending_authority_stale_retry_and_private_result(
    tmp_path: Path,
    backend: str,
    defense: Literal["none", "dodge", "parry"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play, _ = await charged(tmp_path, backend)
    attack = TakeUnarmedTurn(
        id="punch",
        actor_id="a",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        action="punch",
        target_id="b",
        skill="skill:brawling",
        hands=("right-hand",),
        enter_close_combat=True,
    )
    await build_runtime(play).submit_json(cid, attack.model_dump(mode="json"), principal_id="alice")
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_unarmed
    assert pending is not None
    contact = read_contact(state.resources, pending.id)
    assert contact is not None and contact.command_id == "punch" and contact.hand == "right-hand"
    command = ChooseDefense(
        id="respond",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense=defense,
        item_id="left-hand" if defense == "parry" else None,
    )
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    empty = RecordedDice(())
    refused = build_play(tmp_path / "refused", play.engine, store=play.store, rng=empty)
    with pytest.raises(AuthorizationError, match="control"):
        await build_runtime(refused).submit_json(
            cid, command.model_dump(mode="json"), principal_id="alice"
        )
    with pytest.raises(ConflictError):
        await build_runtime(refused).submit_json(
            cid,
            command.model_copy(update={"expected_revision": state.revision - 1}).model_dump(
                mode="json"
            ),
            principal_id="bob",
        )
    assert (
        empty.exhausted()
        and await play.store.read(cid) == before
        and await play.store.history(cid) == history
    )
    dice = RecordedDice(
        (3, 3, 3)
        + (() if defense == "none" else (2, 2, 2))
        + ((1, 2) if defense == "none" else (2,) if defense == "parry" else ())
    )
    from wayfarer.orchestration.combat import generations

    # The saved attack association, not a later defense feature, controls delivery.
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"hand-melee-spell-contact"})
    accepted = build_play(tmp_path / "accept", play.engine, store=play.store, rng=dice)
    await build_runtime(accepted).submit_json(
        cid, command.model_dump(mode="json"), principal_id="bob"
    )
    completed = await play.store.read(cid)
    state = accepted._load(completed)
    result = contact_results(state.resources)[0]
    assert result.triggered == (defense != "dodge")
    assert result.dice == (() if defense == "dodge" else (2,))
    assert casts(state.resources)["hand"].status == ("held" if defense == "dodge" else "spent")
    assert dice.exhausted()
    retry = build_play(tmp_path / "retry", play.engine, store=play.store, rng=RecordedDice(()))
    history = await play.store.history(cid)
    await build_runtime(retry).submit_json(cid, command.model_dump(mode="json"), principal_id="bob")
    assert await play.store.read(cid) == completed and await play.store.history(cid) == history
    # Internal reentry of this actual host-produced receipt also performs no discharge.
    repeated, encounter, recorded = finish_contact(
        retry.rules_context,
        state,
        state.encounters[0],
        contact,
        ordinary_hit=result.ordinary_hit,
        actual_defense=defense,
        defense_check=result.defense_check,
        defense_implement_id=result.defense_implement_id,
        resolved_location=result.location,
        critical_row=None,
    )
    assert repeated == state and encounter == state.encounters[0] and recorded == result
    for route in ("campaign", "stream"):
        caster = await build_runtime(retry).project(route, cid, principal_id="alice")
        rows = validation.sequence(
            validation.decode(json.dumps(caster["hand_melee_spell_findings"]))
        )
        apparent = next(
            validation.mapping(row) for row in rows if "pending_id" in validation.mapping(row)
        )
        assert set(apparent) == {
            "pending_id",
            "cast_id",
            "attacker_id",
            "defender_id",
            "hand",
            "status",
            "outcome",
            "triggered",
            "location",
        }
        assert (
            apparent["pending_id"] == contact.pending_id and apparent["outcome"] == result.outcome
        )
        victim = await build_runtime(retry).project(route, cid, principal_id="bob")
        assert "hand_melee_spell_findings" not in victim
        spectator = await build_runtime(retry).project(route, cid, principal_id="watcher")
        assert "hand_melee_spell_findings" not in spectator
        for view in (caster, victim, spectator):
            encoded = json.dumps(view)
            assert "charge_digest" not in encoded and "mana_event_digest" not in encoded
            assert "hand-melee-spell:" not in encoded
    assert await play.store.replay(cid) == completed


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_charged_attack_without_captured_feature_refuses_before_rng(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.orchestration.combat import generations

    cid, play, _ = await charged(tmp_path, backend)
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"hand-melee-spell-contact"})
    command = TakeUnarmedTurn(
        id="legacy-punch",
        actor_id="a",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        action="punch",
        target_id="b",
        skill="skill:brawling",
        hands=("right-hand",),
        enter_close_combat=True,
    )
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    dice = RecordedDice(())
    play = build_play(tmp_path / "absent", play.engine, store=play.store, rng=dice)
    with pytest.raises(ConflictError, match="captured hand-contact"):
        await CombatService(play).execute(cid, command, principal_id="a")
    assert (
        dice.exhausted()
        and await play.store.read(cid) == before
        and await play.store.history(cid) == history
    )
