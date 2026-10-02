"""B481-482: project loss and last workday follow the actual death instant."""

from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_cyclic_host import prepare as prepare_cyclic
from test_enchanting_projects import setup
from test_enchanting_settlement import assert_reexec
from test_enchanting_source import start

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.enchanting_calendar import EnchantmentRest
from wayfarer.engine.simulation.magic.enchanting_lifecycle import EnchantmentLoss
from wayfarer.engine.simulation.magic.enchanting_transitions import AdvanceEnchanting
from wayfarer.engine.simulation.resources import Advance
from wayfarer.orchestration.cyclic_clock import advance as advance_clock
from wayfarer.orchestration.enchantments import EnchantmentService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_crossed_heart_attack_ends_project_at_death_not_outer_endpoint(
    tmp_path: Path, backend: str
) -> None:
    engine, runtime, foundation = setup(tmp_path)
    state = start(runtime, foundation)
    fp = next(p for p in state.resources.pools if p.id == "fp:b")
    assert fp.fatigue is not None
    terminal = fp.model_copy(
        update={
            "fatigue": fp.fatigue.model_copy(
                update={
                    "heart_attack": True,
                    "unconscious": True,
                    "heart_attack_deadline": 120,
                }
            )
        }
    )
    # A validated saved project already has a canonical impending death. The
    # tested transaction is the real trusted project clock, not a fake event.
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(terminal if p.id == fp.id else p for p in state.resources.pools)
                }
            )
        }
    )
    engine.validate(state)
    play = build_play(tmp_path, engine, backend=backend)
    initial = campaign(engine)
    initial["id"] = state.campaign_id
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    command = AdvanceEnchanting(
        id="after-terminal",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        work_id="begin",
        to=115_200,
    )
    service = EnchantmentService(play)
    outcome = await service.execute(initial["id"], command, principal_id="gm")
    saved = await play.store.read(initial["id"])
    final = play._load(saved)
    death = next(e for e in final.resources.events if e.id == "heart-attack:b:120")
    loss = next(e for e in final.resources.events if e.id == "enchantment-loss:project")
    rest = next(
        e for e in final.resources.events if e.id == "enchantment-rest:enchantment-loss:project"
    )
    assert outcome.status == "mage-lost"
    assert final.resources.game_time == 115_200
    assert death.at == loss.at == rest.at == 120
    assert EnchantmentLoss.model_validate_json(loss.kind).enchanter_ids == ("b",)
    assert EnchantmentRest.model_validate_json(rest.kind).next_shift_at == 86_400
    project = final.resources.enchantment_projects[0]
    assert project.status == "failed" and project.active_work is None
    assert project.energy_completed == 0
    hp = next(p for p in final.resources.pools if p.id == "hp:b")
    assert hp.current == 10 and hp.injury is not None and hp.injury.dead
    assert not next(item for item in final.resources.items if item.id == "blade").enchantments
    assert await service.execute(initial["id"], command, principal_id="gm") == outcome
    assert await play.store.read(initial["id"]) == saved
    await assert_reexec(play, initial, state, tmp_path / "seed-reexecution")


async def test_unenrolled_cyclic_history_keeps_its_original_clock_receipts(tmp_path: Path) -> None:
    cid, play, _ = await prepare_cyclic(tmp_path)
    state = play._load(await play.store.read(cid))
    assert not state.resources.enchantment_projects
    fp = next(p for p in state.resources.pools if p.id == "fp:a")
    assert fp.fatigue is not None
    terminal = fp.model_copy(
        update={
            "fatigue": fp.fatigue.model_copy(
                update={"heart_attack": True, "unconscious": True, "heart_attack_deadline": 5}
            )
        }
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(terminal if p.id == fp.id else p for p in state.resources.pools)
                }
            )
        }
    )
    play.engine.validate(state)
    final = advance_clock(
        play,
        state,
        Advance(
            id="history-clock", actor_id="a", expected_revision=state.resources.revision, to=20
        ),
        RecordedDice((1,) * 100),
    )
    # Captured from the prior released clock: only the original 10/20 boundaries.
    # New enchanting enrollment must not insert a 5-second checkpoint here.
    assert [
        r.command_id for r in final.resources.receipts if r.command_id.startswith("composed-clock:")
    ] == [
        "composed-clock:bf45cca34400ffd8f96ce9cd7c85389ec0ce72afad238240115c896747e69f26",
        "composed-clock:9e82171e3a04dd0a97a981931ac0bf89b3b1ff1c440a363c6c972215cb26e8d7",
    ]
