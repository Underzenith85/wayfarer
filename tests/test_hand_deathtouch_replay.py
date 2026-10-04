"""Original-genesis registered hand casting and saved-contact replay, without Staff."""

import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.hand_deathtouch import fixture, revision
from support.runtime import played
from test_hand_deathtouch_host import prepare_contact
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.magic.hand_melee_contact_state import contact_results
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hand,armor", [("right-hand", False), ("left-hand", True)])
async def test_full_original_genesis_hand_cast_contact_reexecutes_saved_delivery(
    tmp_path: Path,
    backend: str,
    hand: Literal["left-hand", "right-hand"],
    armor: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play, initial = await fixture(tmp_path, backend, defender="armor" if armor else "unarmed")
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await prepare_contact(play, cid, energy=1, hand=hand)
    active = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", active - {"hand-melee-spell-contact"})
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
    monkeypatch.setattr(generations, "ACTIVE", active)
    saved = await play.store.read(cid)
    state = play._load(saved)
    result = contact_results(state.resources)[0]
    assert result.outcome == "discharged" and result.triggered and result.hand == hand
    assert result.injury == sum(result.dice) > 0
    records = await played(play.store, cid)
    attack = next(r for r in records if r.command_id == "punch")
    defense = next(r for r in records if r.command_id == "defense")
    assert "hand-melee-spell-contact" in (attack.command_input or "")
    assert "hand-melee-spell-contact" not in (defense.command_input or "")
    assert all("enchant" not in (r.command_input or "") for r in records)
    ids = {r.command_id for r in records}
    replayed, evidence = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert evidence and all(e.folded and e.reexecuted for e in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)
