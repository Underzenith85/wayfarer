"""Construction/bootstrap proof only; hand casting/contact awaits its new producer."""

from pathlib import Path
from typing import Literal

import pytest
from support.hand_deathtouch import fixture
from support.runtime import played

from wayfarer.engine.simulation.equipment.catalog import MeleeMode


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("defender", ["unarmed", "staff", "armor"])
async def test_original_approved_empty_hand_fixture_without_staff_manufacture(
    tmp_path: Path, backend: str, defender: Literal["unarmed", "staff", "armor"]
) -> None:
    cid, play, initial = await fixture(tmp_path, backend, defender=defender)
    genesis = play._load(initial)
    caster = next(a for a in genesis.actors if a.actor_id == "a")
    build, _ = play.engine.reviewer.activate(
        caster.proposal, caster.approval, campaign_id=cid, actor_id="a"
    )
    assert next(int(v.value) for v in build.sheet.values if v.target == "spell:deathtouch") == 16
    assert next(int(v.value) for v in build.sheet.values if v.target == "skill:brawling") == 13
    assert caster.held_item_hands == ()
    assert not genesis.resources.enchantment_projects and not genesis.resources.events
    assert all(not item.enchantments for item in genesis.resources.items)
    assert genesis.resources.game_time == 0 and genesis.revision == 0
    hp = next(p for p in genesis.resources.pools if p.id == "hp:b")
    assert hp.maximum == hp.current == 10 and hp.injury is not None and not hp.injury.dead
    records = await played(play.store, cid)
    assert [r.command_id for r in records] == ["mana"]
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("missing,magery", [("wither-limb", 2), ("paralyze-limb", 2), (None, 1)])
async def test_missing_source_learning_rejects_before_hand_genesis(
    tmp_path: Path, missing: str | None, magery: int
) -> None:
    with pytest.raises(ValueError, match="Illegal original hand Deathtouch construction"):
        await fixture(tmp_path, "sqlite", missing_prerequisite=missing, magery=magery)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_canonical_close_weapon_and_spectator_are_original_genesis(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await fixture(tmp_path, backend, defender="knife", spectator=True)
    state = play._load(initial)
    defender = next(a for a in state.actors if a.actor_id == "b")
    approved, _ = play.engine.reviewer.activate(
        defender.proposal, defender.approval, campaign_id=cid, actor_id="b"
    )
    assert next(int(v.value) for v in approved.sheet.values if v.target == "skill:knife") == 13
    assert defender.held_item_hands == (("defender-implement", "right-hand"),)
    assert any(m.principal_id == "watcher" and m.role == "spectator" for m in state.members)
    assert (
        play.engine.rules.combat is not None
        and play.engine.rules.combat.gurps_equipment is not None
    )
    knife = next(
        e
        for e in play.engine.rules.combat.gurps_equipment.entries
        if e.definition_id == "equipment:large-knife"
    )
    assert any(
        isinstance(m, MeleeMode) and m.id == "knife-thrust" and 0 in m.reach for m in knife.modes
    )
    assert not state.resources.enchantment_projects
