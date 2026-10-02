"""B239-240: the actual Staff can point at or touch the fixed affected surface."""

from fractions import Fraction
from pathlib import Path

import pytest
from test_item_area_targeting import prepare

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import AreaSelection
from wayfarer.engine.simulation.magic.spells import latest
from wayfarer.engine.simulation.magic.staff_casting_state import (
    DeclareStaffIntent,
    ObserveStaffTouch,
)
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.spells import SpellService, approved_context
from wayfarer.orchestration.staff_casting import StaffCastingService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("contact", [False, True])
async def test_staff_area_pointing_and_trusted_touch_apply_to_nearest_selected_part(
    tmp_path: Path, backend: str, contact: bool
) -> None:
    area = AreaSelection(center=(5, 3), cells=((3, 3), (5, 3)))
    cid, play, start = await prepare(tmp_path, backend, area=area, staff=True, blind=contact)
    assert start.channel_id is not None
    await EnchantmentService(play).declare_staff(
        cid,
        DeclareStaffConstruction(
            id="construction",
            actor_id="a",
            expected_revision=1,
            construction=StaffConstruction(
                item_id="blade",
                definition_id="equipment:sword",
                form="full-staff",
                material="wood",
                once_living=True,
                length_yards=Fraction(2),
            ),
        ),
        principal_id="gm",
    )
    intention = DeclareStaffIntent(
        id="intent",
        actor_id="a",
        expected_revision=2,
        cast_id=start.cast_id,
        spell_id="create-fire",
        channel_id=start.channel_id,
        item_id="blade",
        pointing=True,
        radius=3,
    )
    await StaffCastingService(play).execute(cid, intention, principal_id="a")
    if contact:
        touch = ObserveStaffTouch(
            id="touch", actor_id="a", expected_revision=3, cast_id=start.cast_id
        )
        with pytest.raises(ValidationError, match="director authority"):
            await StaffCastingService(play).execute(cid, touch, principal_id="a")
        await StaffCastingService(play).execute(cid, touch, principal_id="gm")
    before = await play.store.read(cid)
    play.rng = RecordedDice((4, 4, 4))
    cast = start.model_copy(update={"expected_revision": before["revision"]})
    with pytest.raises(ConflictError, match="target changed"):
        await SpellService(play).execute(
            cid, cast.model_copy(update={"radius": 4}), principal_id="a"
        )
    assert await play.store.read(cid) == before
    result = await SpellService(play).execute(cid, cast, principal_id="a")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)[start.cast_id]
    # Trained 14, edge distance2 minus Staff length2. Verified contact also removes -5.
    assert result.checks[0].effective_target == 14
    assert result.outcome == "active" and result.energy_spent == 6
    assert effect.position == (5, 3) and effect.area == area and effect.cost == 6
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 4
    assert tuple(h.actor_id for h in state.resources.hazards if h.active) == ("b",)
    assert await SpellService(play).execute(cid, cast, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_staff_area_needs_an_exact_declared_radius_before_pointing(tmp_path: Path) -> None:
    cid, play, start = await prepare(
        tmp_path, "sqlite", area=AreaSelection(center=(5, 3)), staff=True
    )
    before = await play.store.read(cid)
    assert start.channel_id is not None
    with pytest.raises(ValidationError, match="explicit Area and radius"):
        await StaffCastingService(play).execute(
            cid,
            DeclareStaffIntent(
                id="no-radius",
                actor_id="a",
                expected_revision=1,
                cast_id=start.cast_id,
                spell_id="create-fire",
                channel_id=start.channel_id,
                item_id="blade",
                pointing=True,
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_staff_area_contact_survives_anchor_movement_and_reexecutes(
    tmp_path: Path, backend: str
) -> None:
    import secrets

    from support.runtime import played

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.magic.staff_casting import touching
    from wayfarer.engine.simulation.magic.staff_casting_state import intents
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
    from wayfarer.persistence.replay import verify_commands

    area = AreaSelection(center=(5, 3), cells=((3, 3), (5, 3)))
    cid, play, start = await prepare(
        tmp_path,
        backend,
        area=area,
        staff=True,
        blind=True,
        execution_version=1,
        anchor_target=True,
        seeded=True,
    )
    initial = (await play.store.history(cid))[0].state_after
    assert start.channel_id is not None
    await EnchantmentService(play).declare_staff(
        cid,
        DeclareStaffConstruction(
            id="construction",
            actor_id="a",
            expected_revision=1,
            construction=StaffConstruction(
                item_id="blade",
                definition_id="equipment:sword",
                form="full-staff",
                material="wood",
                once_living=True,
                length_yards=Fraction(2),
            ),
        ),
        principal_id="gm",
    )
    await StaffCastingService(play).execute(
        cid,
        DeclareStaffIntent(
            id="intent",
            actor_id="a",
            expected_revision=2,
            cast_id=start.cast_id,
            spell_id="create-fire",
            channel_id=start.channel_id,
            item_id="blade",
            pointing=True,
            radius=3,
        ),
        principal_id="a",
    )
    await StaffCastingService(play).execute(
        cid,
        ObserveStaffTouch(id="touch", actor_id="a", expected_revision=3, cast_id=start.cast_id),
        principal_id="gm",
    )
    await SpellService(play).execute(
        cid, start.model_copy(update={"expected_revision": 4}), principal_id="a"
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="anchor-leaves",
            actor_id="b",
            expected_revision=5,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=6, y=3),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert touching(play.rules_context, state, intents(state.resources)[0])
    # A source target value is checked separately from the seed-selected outcome.
    assert approved_context(play.rules_context, state, start, area_targeting=True).distance == 0
    play.rng = secrets
    finish = start.model_copy(update={"id": "finish", "kind": "complete", "expected_revision": 6})
    await SpellService(play).execute(cid, finish, principal_id="a")
    final = await play.store.read(cid)
    records = await played(play.store, cid)
    replayed, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks) and replayed == final
    assert latest(play._load(final).resources)[start.cast_id].position == (5, 3)


async def test_actual_caster_loop_invalidates_area_touch_without_erasing_pointing(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.magic.staff_casting import touching
    from wayfarer.engine.simulation.magic.staff_casting_state import intents, invalidated
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn

    area = AreaSelection(center=(5, 3), cells=((3, 3), (5, 3)))
    cid, play, start = await prepare(tmp_path, "sqlite", area=area, staff=True, blind=True)
    await EnchantmentService(play).declare_staff(
        cid,
        DeclareStaffConstruction(
            id="construction",
            actor_id="a",
            expected_revision=1,
            construction=StaffConstruction(
                item_id="blade",
                definition_id="equipment:sword",
                form="full-staff",
                material="wood",
                once_living=True,
                length_yards=Fraction(2),
            ),
        ),
        principal_id="gm",
    )
    assert start.channel_id is not None
    await StaffCastingService(play).execute(
        cid,
        DeclareStaffIntent(
            id="intent",
            actor_id="a",
            expected_revision=2,
            cast_id=start.cast_id,
            spell_id="create-fire",
            channel_id=start.channel_id,
            item_id="blade",
            pointing=True,
            radius=3,
        ),
        principal_id="a",
    )
    await StaffCastingService(play).execute(
        cid,
        ObserveStaffTouch(id="touch", actor_id="a", expected_revision=3, cast_id=start.cast_id),
        principal_id="gm",
    )
    for index, x in enumerate((2, 1)):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id=f"caster-{index}",
                actor_id="a",
                expected_revision=4 + index * 2,
                encounter_id="fight",
                maneuver="move",
                destination=GridPoint(x=x, y=3),
            ),
            principal_id="a",
        )
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id=f"other-{index}",
                actor_id="b",
                expected_revision=5 + index * 2,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
    before = play._load(await play.store.read(cid))
    assert "touch" in invalidated(before.resources)
    assert not touching(play.rules_context, before, intents(before.resources)[0])
    play.rng = RecordedDice((3, 3, 3))
    result = await SpellService(play).execute(
        cid, start.model_copy(update={"expected_revision": 8}), principal_id="a"
    )
    # Returning to the original cell does not resurrect contact: 14 - unseen5.
    assert result.checks[0].effective_target == 9
    assert result.outcome == "active" and result.energy_spent == 6
    assert await play.store.read(cid) == await play.store.replay(cid)
