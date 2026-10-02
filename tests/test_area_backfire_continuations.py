"""B236 retargeting changes the actual Area consequence, retaining old histories."""

from pathlib import Path

import pytest
from support.runtime import played
from test_item_area_targeting import prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import AreaSelection
from wayfarer.engine.simulation.magic.backfires import backfires
from wayfarer.engine.simulation.magic.bindings import BackfireAlternative
from wayfarer.engine.simulation.magic.spells import latest
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.spell_backfires import ResolveSpellBackfire, SpellBackfireService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import verify_commands


def alternative(
    *, center: tuple[int, int] | None = None, targets: tuple[str, ...] = ("a",)
) -> BackfireAlternative:
    return BackfireAlternative(
        id="self-fire",
        spell_id="create-fire",
        rows=(4,),
        effect="retarget",
        target_ids=targets,
        relationship="caster",
        position=center,
        reason="B236 harmful spell affects its caster",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("current", [False, True])
async def test_area_backfire_moves_real_selected_hazard_and_preserves_generation_replay(
    tmp_path: Path, backend: str, current: bool
) -> None:
    choice = alternative()
    cid, play, start = await prepare(
        tmp_path,
        backend,
        radius=1,
        area=AreaSelection(center=(5, 3), cells=((5, 3),)),
        alternatives=(choice,),
        anchor_target=True,
        seeded=True,
    )
    initial = (await play.store.history(cid))[0].state_after
    state = play._load(await play.store.read(cid))
    # First dice 5,6,6 critically fail; the next three dice select B236 row4.
    play.seeds = lambda: format(2222, "064x")
    result = await submit(
        play,
        cid,
        SpellService(play).plan(
            play,
            member_for(state, "a"),
            start,
            principal_id="a",
            state=state,
            area_targeting=current,
        ),
        principal_id="a",
    )
    assert result.outcome == "critical-failure" and result.energy_spent == 2
    state = play._load(await play.store.read(cid))
    pending = backfires(state.resources)[0]
    assert pending.row == 4
    command = ResolveSpellBackfire(
        id="retarget",
        actor_id="gm",
        expected_revision=2,
        backfire_id=pending.id,
        alternative_id=choice.id,
    )
    await SpellBackfireService(play).execute(cid, command, principal_id="gm")
    after = await play.store.read(cid)
    state = play._load(after)
    effect = latest(state.resources)[start.cast_id]
    assert effect.position == (1, 3) and effect.target_id == "a" and effect.radius == 1
    assert effect.area == AreaSelection(
        center=(1, 3) if current else (5, 3), cells=((1, 3),) if current else ((5, 3),)
    )
    assert {h.actor_id for h in state.resources.hazards if h.active} == {"a"}
    assert await SpellBackfireService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == after
    records = await played(play.store, cid)
    final, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks) and final == after
    play.rng = RecordedDice((6,))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="elapsed",
            actor_id="b",
            expected_revision=3,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 5
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10


@pytest.mark.parametrize("reviewed_center", [None, (3, 3)])
async def test_excluded_center_retarget_requires_geometry_that_affects_actual_caster(
    tmp_path: Path, reviewed_center: tuple[int, int] | None
) -> None:
    choice = alternative(center=reviewed_center)
    cid, play, start = await prepare(
        tmp_path,
        "sqlite",
        radius=3,
        area=AreaSelection(center=(5, 3), cells=((3, 3), (4, 3))),
        alternatives=(choice,),
    )
    play.rng = RecordedDice((6, 6, 6, 1, 1, 2))
    await SpellService(play).execute(cid, start, principal_id="a")
    before = await play.store.read(cid)
    pending = backfires(play._load(before).resources)[0]
    command = ResolveSpellBackfire(
        id="retarget",
        actor_id="gm",
        expected_revision=2,
        backfire_id=pending.id,
        alternative_id=choice.id,
    )
    if reviewed_center is None:
        with pytest.raises(ValidationError, match="reviewed center"):
            await SpellBackfireService(play).execute(cid, command, principal_id="gm")
        assert await play.store.read(cid) == before
        return
    await SpellBackfireService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)[start.cast_id]
    assert effect.position == (3, 3) and effect.radius == 3 and effect.cost == 6
    assert effect.area == AreaSelection(center=(3, 3), cells=((1, 3), (2, 3)))
    assert {h.actor_id for h in state.resources.hazards if h.active} == {"a"}


async def test_retarget_outside_current_map_is_atomic(tmp_path: Path) -> None:
    choice = alternative()
    cid, play, start = await prepare(
        tmp_path, "sqlite", radius=3, area=AreaSelection(center=(5, 3)), alternatives=(choice,)
    )
    play.rng = RecordedDice((6, 6, 6, 1, 1, 2))
    await SpellService(play).execute(cid, start, principal_id="a")
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="outside the battlefield"):
        await SpellBackfireService(play).execute(
            cid,
            ResolveSpellBackfire(
                id="outside",
                actor_id="gm",
                expected_revision=2,
                backfire_id=backfires(play._load(before).resources)[0].id,
                alternative_id=choice.id,
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before


async def test_every_random_backfire_candidate_is_validated_before_any_draw(tmp_path: Path) -> None:
    choice = alternative(center=(1, 3), targets=("a", "b")).model_copy(
        update={"rows": (7,), "relationship": "any"}
    )
    cid, play, start = await prepare(
        tmp_path,
        "sqlite",
        radius=1,
        area=AreaSelection(center=(5, 3), cells=((5, 3),)),
        alternatives=(choice,),
    )
    play.rng = RecordedDice((6, 6, 6, 2, 2, 3))
    await SpellService(play).execute(cid, start, principal_id="a")
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="reviewed center"):
        await SpellBackfireService(play).execute(
            cid,
            ResolveSpellBackfire(
                id="random-invalid",
                actor_id="gm",
                expected_revision=2,
                backfire_id=backfires(play._load(before).resources)[0].id,
                alternative_id=choice.id,
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
