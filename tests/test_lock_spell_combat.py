"""B236 consecutive Concentrate turns and B383 later Ready on real lock state."""

from pathlib import Path

import pytest
from test_lock_spell_persistence import declare, prepare, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.lock_bindings import LockChannel
from wayfarer.engine.simulation.magic.lock_host import DeclareLockChannel
from wayfarer.engine.simulation.magic.lock_state import latest as locks
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellResult
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.locks import LockService, LockSpellService
from wayfarer.orchestration.play import PlayService


async def prepare_fight(path: Path, backend: str) -> tuple[str, PlayService]:
    cid, play = await prepare(path, backend, combat=True)
    await declare(play, cid)
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=3,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=3, y=1)),
            ),
        ),
        principal_id="gm",
    )
    for spell in ("magelock", "lockmaster"):
        await LockService(play).execute(
            cid,
            DeclareLockChannel(
                id="combat-channel:" + spell,
                actor_id="gm",
                expected_revision=await revision(play, cid),
                channel=LockChannel.model_validate(
                    dict(
                        id="combat:" + spell,
                        actor_id="a",
                        target_id="chest",
                        location_id="dock",
                        spell_id=spell,
                        encounter_id="fight",
                        position=(2, 1),
                    )
                ),
            ),
            principal_id="gm",
        )
    return cid, play


async def other_turn(cid: str, play: PlayService, identifier: str) -> None:
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id=identifier,
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )


async def cast_in_combat(
    cid: str, play: PlayService, *, spell: str, turns: int, dice: tuple[int, ...]
) -> SpellResult:
    result = SpellResult(outcome="casting")
    for turn in range(turns):
        play.rng = RecordedDice(dice if turn == turns - 1 else ())
        result = await LockSpellService(play).execute(
            cid,
            RuntimeSpellCommand.model_validate(
                dict(
                    id=f"{spell}:{turn}",
                    actor_id="a",
                    expected_revision=await revision(play, cid),
                    kind="start" if turn == 0 else "concentrate",
                    spell_id=spell,
                    cast_id=spell,
                    channel_id="combat:" + spell,
                )
            ),
            principal_id="gm",
        )
        if turn < turns - 1:
            assert result.outcome == "casting"
        play.rng = RecordedDice(())
        await other_turn(cid, play, f"other:{spell}:{turn}")
    return result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_combat_lock_spells_use_real_turns_and_later_ready_opening(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_fight(tmp_path, backend)
    result = await cast_in_combat(cid, play, spell="magelock", turns=4, dice=(3, 3, 3))
    assert result.outcome == "active" and result.energy_spent == 3
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 4
    assert latest(state.resources)["magelock"].skill == 13
    ready = TakeCombatTurn(
        id="opening",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        target_id="chest",
    )
    with pytest.raises(ConflictError, match="prevents opening"):
        await CombatService(play).execute(cid, ready, principal_id="a")
    before = await play.store.read(cid)
    result = await cast_in_combat(cid, play, spell="lockmaster", turns=10, dice=(3, 3, 3, 4, 4, 4))
    assert result.outcome == "active" and result.energy_spent == 3
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 14
    assert not locks(state.resources)["chest"].locked
    assert latest(state.resources)["magelock"].phase == "ended"
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 4
    assert result.checks[0].effective_target == 12 and result.checks[1].effective_target == 13
    ready = ready.model_copy(update={"expected_revision": state.revision})
    await CombatService(play).execute(cid, ready, principal_id="a")
    after = await play.store.read(cid)
    assert after != before and not locks(play._load(after).resources)["chest"].closed
    assert await play.store.replay(cid) == after
    await CombatService(play).execute(cid, ready, principal_id="a")
    assert await play.store.read(cid) == after


@pytest.mark.parametrize(
    "position,geometry", [((-1, 1), "square"), ((5, 1), "square"), ((2, 1), "hex")]
)
async def test_invalid_combat_channel_does_not_poison_later_object_placement(
    tmp_path: Path, position: tuple[int, int], geometry: str
) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await prepare_fight(tmp_path, "sqlite")
    before = await play.store.read(cid)
    command = DeclareLockChannel(
        id="additional-channel",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        channel=LockChannel.model_validate(
            dict(
                id="bad-channel",
                actor_id="a",
                target_id="chest",
                location_id="dock",
                spell_id="magelock",
                encounter_id="fight",
                position=position,
                geometry=geometry,
            )
        ),
    )
    with pytest.raises(ValidationError, match="outside|geometry"):
        await LockService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before
    fixed = command.model_copy(
        update={
            "channel": command.channel.model_copy(update={"position": (2, 1), "geometry": "square"})
        }
    )
    await LockService(play).execute(cid, fixed, principal_id="gm")
    assert await play.store.replay(cid) == await play.store.read(cid)


@pytest.mark.parametrize("dice,opened", [((3, 3, 3), True), ((4, 4, 4), False)])
async def test_close_combat_ready_changes_door_only_after_successful_dx(
    tmp_path: Path, dice: tuple[int, ...], opened: bool
) -> None:
    from wayfarer.contracts import Campaign, CommandReceipt
    from wayfarer.engine.simulation.combat.spatial import SquareActorPlacement

    cid, play = await prepare_fight(tmp_path, "sqlite")
    await cast_in_combat(cid, play, spell="lockmaster", turns=10, dice=(3, 3, 3))
    before = play._load(await play.store.read(cid))
    encounter = before.encounters[0]
    point = next(p.position for p in encounter.participants if p.actor_id == "a")
    assert isinstance(point, GridPoint)
    encounter = encounter.replace_placement(SquareActorPlacement(actor_id="b", position=point))
    encounter = encounter.model_copy(
        update={
            "participants": tuple(
                p.model_copy(update={"position": point}) if p.actor_id == "b" else p
                for p in encounter.participants
            ),
            "close_pairs": (("a", "b"),),
        }
    )

    def prior_close_combat(campaign: Campaign) -> CommandReceipt:
        updated = before.model_copy(
            update={
                "revision": before.revision + 1,
                "resources": before.resources.model_copy(update={"revision": before.revision + 1}),
                "encounters": (encounter,),
            }
        )
        play.commit(campaign, updated)
        return CommandReceipt(action="resource", outcome="fixture.close-combat")

    await play.store.commit_turn(
        cid, "close-combat", before.revision, "close-combat", prior_close_combat, actor_id="gm"
    )
    command = TakeCombatTurn(
        id="ready-door",
        actor_id="a",
        expected_revision=before.revision + 1,
        encounter_id="fight",
        maneuver="ready",
        target_id="chest",
        ready_hand="right-hand",
    )
    play.rng = RecordedDice(dice)
    await CombatService(play).execute(cid, command, principal_id="a")
    after = await play.store.read(cid)
    saved = play._load(after)
    assert locks(saved.resources)["chest"].closed is not opened
    assert len([e for e in saved.resources.events if e.id.startswith("close-ready:")]) == 1
    play.rng = RecordedDice(())
    await CombatService(play).execute(cid, command, principal_id="a")
    assert after == await play.store.read(cid) == await play.store.replay(cid)
