"""B556/B363 balance restrictions reach working spell and ability services."""

from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_abilities import command as ability_command
from test_abilities import spec
from test_ability_service import setup
from test_innate_criticals import context, fight, miss, table
from test_spell_service import resolve
from test_spells import command as spell_command

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.spells import latest as spells
from wayfarer.engine.simulation.traits.innate_criticals import resolve_innate_miss
from wayfarer.errors import ValidationError
from wayfarer.orchestration.abilities import AbilityService
from wayfarer.orchestration.spells import SpellService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("family", ["spell", "ability"])
async def test_completed_encounter_balance_blocks_then_elapsed_turn_allows_real_action(
    tmp_path: Path, backend: str, family: str
) -> None:
    cid, original = await setup(tmp_path / "setup", spec(), magic=True)
    initial = await original.store.read(cid)
    state = original._load(initial)
    resources, _, outcome = resolve_innate_miss(
        state.resources,
        fight(),
        context().model_copy(update={"campaign_id": cid}),
        miss(),
        rng=RecordedDice(table(7)),
        system=True,
    )
    assert outcome.effect == "lose-balance"
    # The campaign checkpoint retains the finished encounter's actual outcome;
    # no live encounter remains to advance this actor's HP turn counter.
    original.commit(initial, state.model_copy(update={"resources": resources}))
    play = build_play(tmp_path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, initial)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="even free actions"):
        if family == "spell":
            await SpellService(play, resolve).execute(cid, spell_command(), principal_id="gm")
        else:
            await AbilityService(play).execute(cid, ability_command(), principal_id="a")
    assert await play.store.read(cid) == before
    await play.execute(
        cid, Wait(id="next-second", actor_id="a", expected_revision=0, ticks=1), principal_id="a"
    )
    if family == "spell":
        command = spell_command(1)
        result = await SpellService(play, resolve).execute(cid, command, principal_id="gm")
        after = await play.store.read(cid)
        assert any(
            e.phase == "casting" and e.ready_at == 2
            for e in spells(play._load(after).resources).values()
        )
        assert await SpellService(play, resolve).execute(cid, command, principal_id="gm") == result
    else:
        command_a = ability_command(1)
        result_a = await AbilityService(play).execute(cid, command_a, principal_id="a")
        after = await play.store.read(cid)
        actor = next(a for a in play._load(after).actors if a.actor_id == "a")
        assert actor.available_at == 2
        assert await AbilityService(play).execute(cid, command_a, principal_id="a") == result_a
    assert play._load(after).resources.game_time == 1
    assert play._load(after).revision == 2
    assert await play.store.read(cid) == after == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_critical_blocks_free_inventory_drop_until_first_next_turn(
    tmp_path: Path, backend: str
) -> None:
    from test_composed_attack_host import declare, defense, fixture, idle

    from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand
    from wayfarer.orchestration.size_forms import SizeFormService

    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    play.rng = RecordedDice((6, 6, 6, 1, 1, 5))
    await defense(play, cid)
    before = await play.store.read(cid)
    state = play._load(before)
    assert state.encounters[0].current_actor_id == "b"
    command = WorldGroundCommand(
        id="free-drop",
        actor_id="a",
        expected_revision=state.revision,
        kind="drop",
        item_id="sword-a",
    )
    with pytest.raises(ValidationError, match="even free actions"):
        await SizeFormService(play).retrieve(cid, command, principal_id="alice")
    assert await play.store.read(cid) == before
    play.rng = RecordedDice(())
    await idle(play, cid)
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a"
    command = command.model_copy(update={"expected_revision": state.revision})
    await SizeFormService(play).retrieve(cid, command, principal_id="alice")
    saved = await play.store.read(cid)
    dropped = next(i for i in play._load(saved).resources.items if i.id == "sword-a")
    assert dropped.world_ground_location_id == "dock" and not dropped.ready and not dropped.equipped
    await SizeFormService(play).retrieve(cid, command, principal_id="alice")
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
