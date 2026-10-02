"""B481 nighttime play and exact next-shift admission through the real services."""

import asyncio
import secrets
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_enchanting_projects import setup
from test_enchanting_source import source_recipe

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.magic.bindings import SpellChannel
from wayfarer.engine.simulation.magic.effects import illuminated, lighting_penalty
from wayfarer.engine.simulation.magic.enchanting_calendar import enchanting_work_active
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    SettleEnchanting,
)
from wayfarer.engine.simulation.magic.spells import SpellCommand, latest
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import verify_commands


async def night_campaign(tmp_path: Path, backend: str) -> tuple[Campaign, PlayService, PlayState]:
    original, _, foundation = setup(tmp_path, learn_source_spells=True)
    enchanting, spells = original.rules.enchanting, original.rules.spells
    assert enchanting is not None and spells is not None
    rules = original.rules.model_copy(
        update={
            "enchanting": enchanting.model_copy(update={"recipes": (source_recipe("staff"),)}),
            "spells": spells.model_copy(
                update={
                    "execution_version": 2,
                    "channels": tuple(
                        SpellChannel(
                            id="personal-" + spell,
                            actor_id="a",
                            target_id="a",
                            location_id="forge",
                            spell_id=spell,
                        )
                        for spell in ("light", "daze")
                    ),
                }
            ),
        }
    )
    engine = ActionEngine(original.reviewer, original.resources, rules)
    play = build_play(tmp_path, engine, backend=backend, rng=secrets)
    initial = campaign(engine)
    actors = tuple(
        ActorSetup(
            actor_id=actor.actor_id,
            proposal=actor.proposal.model_copy(
                update={
                    "draft": actor.proposal.draft.model_copy(
                        update={
                            "purchases": actor.proposal.draft.purchases
                            + tuple(
                                Purchase(definition_id="spell:" + spell, amount=4)
                                for spell in ("foolishness", "daze")
                            )
                        }
                    )
                }
            ),
        )
        for actor in foundation.actors
    )
    state = play.initial_state(initial, foundation.world, foundation.resources, actors)
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    service = EnchantmentService(play)
    await service.declare_staff(
        cid,
        DeclareStaffConstruction(
            id="staff-fact",
            actor_id="a",
            expected_revision=0,
            construction=StaffConstruction(
                item_id="blade",
                definition_id="equipment:sword",
                form="wand",
                length_yards=Fraction(1),
                material="reed",
                once_living=True,
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        CreateEnchantment(
            id="create-staff",
            actor_id="a",
            expected_revision=1,
            project_id="staff-project",
            recipe_id=source_recipe("staff").id,
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        BeginEnchanting(
            id="work-staff", actor_id="a", expected_revision=2, project_id="staff-project"
        ),
        principal_id="gm",
    )
    await advance_work(play, cid, 28_800)
    return initial, play, state


async def advance_work(play: PlayService, cid: str, to: int) -> None:
    current = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id=f"work-to-{to}",
            actor_id="a",
            expected_revision=current.revision,
            project_id="staff-project",
            work_id="work-staff",
            to=to,
        ),
        principal_id="gm",
    )


def light(identifier: str, revision: int, *, complete: bool = False) -> SpellCommand:
    return SpellCommand(
        id=identifier,
        actor_id="a",
        expected_revision=revision,
        kind="complete" if complete else "start",
        spell_id="light",
        channel_id="personal-light",
        cast_id="night-light",
    )


async def assert_reexecution(
    initial: Campaign, play: PlayService, genesis: PlayState, tmp_path: Path
) -> None:
    cid = initial["id"]
    saved = await play.store.read(cid)
    assert await play.store.replay(cid) == saved
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=genesis.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(check.folded and check.reexecuted for check in checks), checks
    assert final == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_night_light_and_wait_preserve_staff_work_and_reexecute(
    tmp_path: Path, backend: str
) -> None:
    initial, play, genesis = await night_campaign(tmp_path, backend)
    cid = initial["id"]
    service = SpellService(play)
    before = play._load(await play.store.read(cid))
    project = before.resources.enchantment_projects[0]
    assert before.resources.game_time == 28_800
    assert project.active_work is not None and project.active_work.due == 1_238_400
    assert not enchanting_work_active(before.resources, project)
    assert project.energy_completed == 0
    start = light("start-light", before.revision)
    saved = await play.store.read(cid)
    # Other timed services retain their original admission until their complete
    # intervals are joined; this repair does not open unchecked travel/checks.
    with pytest.raises(ConflictError, match="committed to enchanting"):
        await NoncombatService(play).execute(
            cid,
            NoncombatCommand(
                id="unjoined-approach",
                actor_id="a",
                expected_revision=before.revision,
                kind="approach_noncombat",
                encounter_id="unjoined-encounter",
                selection_id="unjoined-check",
            ),
            principal_id="a",
        )
    with pytest.raises(AuthorizationError, match="not controlled"):
        await service.execute(cid, start, principal_id="b")
    assert await play.store.read(cid) == saved
    started = await service.execute(cid, start, principal_id="a")
    wait = Wait(id="casting-second", actor_id="a", expected_revision=5, ticks=1)
    waited = await play.execute(cid, wait, principal_id="a")
    complete = light("complete-light", 6, complete=True)
    results = await asyncio.gather(
        *(service.execute(cid, complete, principal_id="a") for _ in range(2))
    )
    assert results[0] == results[1]
    result = results[0]
    assert result.outcome == "active" and result.energy_spent == 0
    assert result.checks[0].effective_target == 18
    current = play._load(await play.store.read(cid))
    assert current.resources.game_time == 28_801
    assert latest(current.resources)["night-light"].expires_at == 28_861
    assert illuminated(current, "a") and lighting_penalty(current, "a", -9) == -3
    assert not illuminated(current, "b")
    assert current.resources.enchantment_projects[0] == project
    assert [(pool.id, pool.current) for pool in current.resources.pools if pool.fatigue] == [
        ("fp:a", 10),
        ("fp:b", 10),
    ]
    for ticks, expected_light in ((59, True), (1, False)):
        await play.execute(
            cid,
            Wait(
                id=f"light-expiry-{ticks}",
                actor_id="a",
                expected_revision=current.revision,
                ticks=ticks,
            ),
            principal_id="a",
        )
        current = play._load(await play.store.read(cid))
        assert illuminated(current, "a") is expected_light
    # Nighttime activity does not authorize a second shift on this project.
    saved = await play.store.read(cid)
    with pytest.raises(ConflictError, match="not available for work"):
        await EnchantmentService(play).execute(
            cid,
            BeginEnchanting(
                id="second-shift",
                actor_id="a",
                expected_revision=current.revision,
                project_id="staff-project",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == saved
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await SpellService(restarted).execute(cid, start, principal_id="a") == started
    assert await restarted.execute(cid, wait, principal_id="a") == waited
    assert await SpellService(restarted).execute(cid, complete, principal_id="a") == result
    with pytest.raises(ConflictError, match="changed"):
        await SpellService(restarted).execute(
            cid, start.model_copy(update={"id": "stale"}), principal_id="a"
        )
    with pytest.raises(ConflictError):
        await SpellService(restarted).execute(
            cid, start.model_copy(update={"cast_id": "different"}), principal_id="a"
        )
    assert await restarted.store.read(cid) == saved
    await advance_work(play, cid, 1_238_400)
    completed = await EnchantmentService(play).execute(
        cid,
        SettleEnchanting(
            id="settle-staff",
            actor_id="a",
            expected_revision=10,
            project_id="staff-project",
            work_id="work-staff",
        ),
        principal_id="gm",
    )
    current = play._load(await play.store.read(cid))
    assert current.resources.game_time == 1_238_400
    assert completed.energy_completed == 30
    assert current.resources.enchantment_projects[0].active_work is None
    assert all(pool.current == 10 for pool in current.resources.pools if pool.fatigue)
    await assert_reexecution(initial, play, genesis, tmp_path)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("project_clock", [False, True])
async def test_next_shift_crossing_rejects_atomically_but_exact_completion_is_legal(
    tmp_path: Path, backend: str, cancel: bool, project_clock: bool
) -> None:
    initial, play, genesis = await night_campaign(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, 86_399)
    before = await play.store.read(cid)
    current = play._load(before)
    service = SpellService(play)
    with pytest.raises(ConflictError, match="overlaps the next"):
        await service.execute(
            cid,
            SpellCommand(
                id="crossing-daze",
                actor_id="a",
                expected_revision=current.revision,
                kind="start",
                spell_id="daze",
                channel_id="personal-daze",
                cast_id="crossing-daze",
            ),
            principal_id="a",
        )
    with pytest.raises(ConflictError, match="overlaps the next"):
        await play.execute(
            cid,
            Wait(id="crossing-wait", actor_id="a", expected_revision=current.revision, ticks=2),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    assert latest(current.resources) == {}
    await service.execute(cid, light("boundary-start", current.revision), principal_id="a")
    pending = await play.store.read(cid)
    with pytest.raises(ConflictError, match="overlaps the next"):
        await advance_work(play, cid, 86_401)
    assert await play.store.read(cid) == pending
    if project_clock:
        await advance_work(play, cid, 86_400)
    else:
        await play.execute(
            cid,
            Wait(id="boundary-second", actor_id="a", expected_revision=6, ticks=1),
            principal_id="a",
        )
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=secrets)
    finish = light("boundary-finish", 7, complete=True)
    if cancel:
        finish = finish.model_copy(update={"kind": "cancel"})
    saved = await restarted.store.read(cid)
    with pytest.raises(AuthorizationError, match="not controlled"):
        await SpellService(restarted).execute(cid, finish, principal_id="b")
    with pytest.raises(ConflictError, match="changed"):
        await SpellService(restarted).execute(
            cid, finish.model_copy(update={"expected_revision": 6}), principal_id="a"
        )
    assert await restarted.store.read(cid) == saved
    result = await SpellService(restarted).execute(cid, finish, principal_id="a")
    assert result.outcome == ("cancelled" if cancel else "active")
    assert result.energy_spent == 0
    if not cancel:
        assert result.checks[0].effective_target == 18
    current = restarted._load(await restarted.store.read(cid))
    assert current.resources.game_time == 86_400
    assert illuminated(current, "a") is not cancel
    assert latest(current.resources)["night-light"].phase == ("ended" if cancel else "active")
    assert await SpellService(restarted).execute(cid, finish, principal_id="a") == result
    project = current.resources.enchantment_projects[0]
    assert enchanting_work_active(current.resources, project)
    assert project.active_work is not None and project.active_work.due == 1_238_400
    before = await restarted.store.read(cid)
    with pytest.raises(ConflictError, match="committed to enchanting"):
        await SpellService(restarted).execute(
            cid,
            light("during-shift", current.revision).model_copy(update={"cast_id": "new-light"}),
            principal_id="a",
        )
    with pytest.raises(ConflictError, match="committed to enchanting"):
        await restarted.execute(
            cid,
            Wait(id="during-work", actor_id="b", expected_revision=current.revision, ticks=1),
            principal_id="b",
        )
    assert await restarted.store.read(cid) == before
    await assert_reexecution(initial, restarted, genesis, tmp_path)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_clock_cannot_strand_a_cast_that_missed_its_boundary_completion(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await night_campaign(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, 86_398)
    await SpellService(play).execute(cid, light("early-start", 5), principal_id="a")
    before = await play.store.read(cid)
    # The assistant's ordinary Wait uses the same clock as the lead's pending cast.
    with pytest.raises(ConflictError, match="Complete or cancel"):
        await play.execute(
            cid,
            Wait(id="missed-finish", actor_id="b", expected_revision=6, ticks=2),
            principal_id="b",
        )
    assert await play.store.read(cid) == before
    await play.execute(
        cid, Wait(id="reach-finish", actor_id="b", expected_revision=6, ticks=1), principal_id="b"
    )
    result = await SpellService(play).execute(
        cid, light("finish-before-shift", 7, complete=True), principal_id="a"
    )
    assert result.outcome == "active"
    await play.execute(
        cid, Wait(id="reach-shift", actor_id="b", expected_revision=8, ticks=1), principal_id="b"
    )
    assert play._load(await play.store.read(cid)).resources.game_time == 86_400
