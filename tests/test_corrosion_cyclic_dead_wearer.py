"""Actual lethal attacks preserve subsequent Cyclic corrosion of worn armor."""

from pathlib import Path

import pytest
from support.runtime import played
from test_composed_attack_host import declare
from test_corrosion_armor import total_loss
from test_corrosion_composed_host import defend, establish_beam_knowledge, fixture

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.types.cyclic import CyclicOccurrence
from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
from wayfarer.engine.simulation.traits.harmful_physiology_state import AdvancePhysiology
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_dead_wearer_keeps_eroding_armor_without_hp_or_debt(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source, initial = await fixture(
        tmp_path, backend, repeated=True, innate_beam_skill=True
    )
    await establish_beam_knowledge(play, cid)
    await declare(play, cid, source)
    await defend(play, cid)
    state = play._load(await play.store.read(cid))
    assert total_loss(state.resources, "plate-b") == 1
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 8
    play.seeds = lambda: f"{53:064x}"
    for index in range(5):
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id=f"wait-{index}",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id=f"sword-{index}",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="swing",
                target_id="b",
            ),
            principal_id="a",
        )
        await defend(play, cid, f"sword-defense-{index}", principal="b")
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury and hp.injury.dead and hp.current == -12
    debt = state.resources.cyclic_attacks[0].hp_debt
    assert debt == 2
    assert state.encounters[0].status == "completed"
    play.seeds = lambda: f"{1:064x}"
    commands = []
    for at, loss, remaining in ((10, 2, 1), (20, 3, 0)):
        state = play._load(await play.store.read(cid))
        command = AdvancePhysiology(
            id=f"tick-{at}", actor_id="a", expected_revision=state.revision, to=at
        )
        commands.append(command)
        await HarmfulPhysiologyService(play).execute(cid, command, principal_id="gm")
        state = play._load(await play.store.read(cid))
        assert next(p for p in state.resources.pools if p.id == "hp:b") == hp
        assert total_loss(state.resources, "plate-b") == loss
        attack = state.resources.cyclic_attacks[0]
        assert attack.hp_debt == debt and attack.fp_debt == 0
        assert attack.remaining == remaining and attack.cycle == loss
        assert attack.due == at + 10 and attack.active == bool(remaining)
        event = next(e for e in reversed(state.resources.events) if e.id.startswith("cyclic:"))
        occurrence = CyclicOccurrence.model_validate_json(event.kind)
        assert not any(r.command_id == occurrence.id for r in state.resources.receipts)
        assert occurrence.damage_dice == (1, 5)
        assert occurrence.hp_lost == occurrence.fp_lost == 0 and occurrence.check is None
    final = await play.store.read(cid)
    for command in commands:
        await HarmfulPhysiologyService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)
    # Event folding may reorder resource JSON object keys; compare canonical state.
    assert {k: v for k, v in replayed.items() if k != "play_json"} == {
        k: v for k, v in final.items() if k != "play_json"
    }
