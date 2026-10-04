"""Approved purchases and immutable initial health, followed by real Rooted combat."""

import secrets
from pathlib import Path

from support.rooted_feet import fixture as root_fixture
from support.rooted_feet import observe, revision
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet, active_effect
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.rooted_feet import RootedFeetService


async def fixture(
    path: Path, backend: str, *, dodge: int = 10, hp: int = 12, fp: int = 12, cr: bool = False
) -> tuple[str, PlayService, Campaign]:
    return await root_fixture(
        path,
        backend,
        combat_weapons=True,
        subject_st=12,
        subject_ht=12,
        subject_dx=(dodge - 3) * 4 - 12,
        subject_hp=hp,
        subject_fp=fp,
        subject_purchases=(Purchase(definition_id="trait:combat-reflexes"),) if cr else (),
    )


async def prepare(play: PlayService, cid: str, *, seeded: bool = False) -> None:
    await observe(play, cid, target="b")
    play.rng = secrets if seeded else RecordedDice((3, 3, 3, 6, 6, 6))
    play.seeds = lambda: f"{2:064x}"
    await RootedFeetService(play).execute(
        cid,
        CastRootedFeet(
            id="root",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="root",
            subject_id="subject",
        ),
        principal_id="cora",
    )
    assert active_effect(play._load(await play.store.read(cid)).resources, "b") is not None
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="fight",
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
    state = play._load(await play.store.read(cid))
    while state.encounters[0].current_actor_id != "a":
        actor = state.encounters[0].current_actor_id
        await service.execute(
            cid,
            TakeCombatTurn(
                id="wait-" + actor,
                actor_id=actor,
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id=actor,
        )
        state = play._load(await play.store.read(cid))
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        principal_id="a",
    )
