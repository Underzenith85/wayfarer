"""Original-genesis paid Haste a→b and independent Rooted caster c producers."""

import secrets
from pathlib import Path
from typing import Literal

from support.rooted_feet import fixture as rooted_fixture
from support.rooted_feet import observe, revision
from support.runtime import build_runtime
from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.haste_state import DeclareHasteChannel, HasteChannel
from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet, effects
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.play import PlayService


async def fixture(
    path: Path,
    backend: str,
    *,
    subject_dx: int | None = None,
    subject_ht: int | None = None,
    combat_weapons: bool = True,
    ranged_weapon: bool = False,
) -> tuple[str, PlayService, Campaign]:
    return await rooted_fixture(
        path,
        backend,
        subject_dx=subject_dx,
        subject_ht=subject_ht,
        subject_fp=subject_ht if subject_ht is not None else 10,
        combat_weapons=combat_weapons,
        ranged_weapon=ranged_weapon,
        bidirectional_visibility=True,
    )


async def cast_haste(
    play: PlayService, cid: str, *, energy: int = 3, seed: int = 1, identity: str = "haste"
) -> None:
    if energy not in (1, 2, 3):
        raise ValueError("Source Haste energy must be 1, 2 or 3")
    runtime = build_runtime(play)
    service = HasteService(play)
    play.rng = secrets
    play.seeds = lambda: f"{seed:064x}"
    await service.execute(
        cid,
        DeclareHasteChannel(
            id=identity + "-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=HasteChannel(id=identity, actor_id="a", target_id="b", location_id="dock"),
        ),
        principal_id="gm",
    )

    async def command(kind: Literal["start", "concentrate", "complete"]) -> None:
        await service.execute(
            cid,
            RuntimeSpellCommand(
                id=identity + "-" + kind,
                actor_id="a",
                expected_revision=await revision(play, cid),
                kind=kind,
                spell_id="haste",
                cast_id=identity,
                channel_id=identity,
                energy=energy,
            ),
            principal_id="alice",
        )

    await command("start")
    await runtime.submit_json(
        cid,
        Wait(
            id=identity + "-wait-one",
            actor_id="a",
            expected_revision=await revision(play, cid),
            ticks=1,
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    await command("concentrate")
    await runtime.submit_json(
        cid,
        Wait(
            id=identity + "-wait-two",
            actor_id="a",
            expected_revision=await revision(play, cid),
            ticks=1,
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    await command("complete")


async def cast_rooted(play: PlayService, cid: str, *, seed: int = 2) -> None:
    await observe(play, cid)
    play.rng = secrets
    play.seeds = lambda: f"{seed:064x}"
    await build_runtime(play).submit_json(
        cid,
        CastRootedFeet(
            id="root",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="root",
            subject_id="subject",
        ).model_dump(mode="json"),
        principal_id="cora",
    )
    assert effects(play._load(await play.store.read(cid)).resources)["root"].status == "active"


async def prepare_composition(
    play: PlayService, cid: str, *, energy: int = 3, order: Literal["before", "after"] = "before"
) -> None:
    if order == "before":
        await cast_haste(play, cid, energy=energy)
        await cast_rooted(play, cid)
    else:
        await cast_rooted(play, cid)
        await cast_haste(play, cid, energy=energy)
