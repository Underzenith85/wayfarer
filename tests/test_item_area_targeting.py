"""B239/B482 source-derived Area geometry, sight and actual item consequences."""

import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_magic_item_vision import prepare as item_campaign
from test_staff_casting import prepare as staff_campaign
from test_staff_casting_vision import symptoms

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.magic.protocols import AreaSelection
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.simulation.magic.bindings import BackfireAlternative, SpellChannel
from wayfarer.engine.simulation.magic.casting_targeting import targeting
from wayfarer.engine.simulation.magic.spells import PROFILE, SpellCommand, latest
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService, approved_context
from wayfarer.persistence.replay import command_text, verify_commands


async def prepare(
    path: Path,
    backend: str,
    *,
    area: AreaSelection | None = None,
    radius: int = 3,
    b: tuple[int, int] = (5, 3),
    blind: bool = False,
    reduction: int = 0,
    power: int = 15,
    execution_version: Literal[1, 2] = 2,
    hexes: bool = False,
    opaque: tuple[tuple[int, int], ...] = (),
    seeded: bool = False,
    staff: bool = False,
    anchor_target: bool = False,
    darkness: bool = False,
    alternatives: tuple[BackfireAlternative, ...] = (),
) -> tuple[str, PlayService, SpellCommand]:
    if staff:
        cid, foundation, _ = await staff_campaign(path / "foundation", backend, combat=True)
        start = SpellCommand(
            id="start",
            actor_id="a",
            expected_revision=0,
            kind="start",
            spell_id="create-fire",
            channel_id="create-fire",
            cast_id="area-cast",
        )
    else:
        cid, foundation, start = await item_campaign(
            path / "foundation", backend, spell="create-fire", distance=4, combat=True
        )
    old = foundation._load(await foundation.store.read(cid))
    rules = foundation.engine.rules
    assert rules.spells is not None and rules.combat is not None
    channel = next(c for c in rules.spells.channels if c.spell_id == "create-fire").model_copy(
        update={
            "area": area,
            "target_id": "forge" if area is not None and not anchor_target else "b",
        }
    )
    rules = rules.model_copy(
        update={
            "spells": rules.spells.model_copy(
                update={
                    "execution_version": execution_version,
                    "backfire_alternatives": alternatives,
                    "channels": (channel,)
                    + (
                        (
                            SpellChannel(
                                id="illuminate",
                                actor_id="a",
                                target_id="b",
                                location_id="forge",
                                spell_id="light",
                            ),
                        )
                        if darkness
                        else ()
                    ),
                    "magic_items": tuple(
                        i.model_copy(update={"power_reduction": reduction, "power": power})
                        for i in rules.spells.magic_items
                    ),
                }
            )
        }
    )
    if darkness:
        assert rules.combat is not None
        rules = rules.model_copy(
            update={
                "combat": rules.combat.model_copy(
                    update={
                        "battlefields": tuple(
                            f.model_copy(update={"darkness_penalty": -10})
                            for f in rules.combat.battlefields
                        )
                    }
                )
            }
        )
    if hexes:
        assert rules.combat is not None
        rules = rules.model_copy(
            update={
                "combat": rules.combat.model_copy(
                    update={
                        "battlefields": (
                            HexBattlefield(
                                id="forge",
                                coordinate_system="hex-axial-v1",
                                profile_id=PROFILE,
                                baseline_id=BASELINE_ID,
                                darkness_penalty=-10 if darkness else 0,
                                location_id="forge",
                                cells=tuple(
                                    Cell(
                                        position=Hex(q=x, r=y),
                                        opaque_height=3 if (x, y) in opaque else 0,
                                    )
                                    for x in range(10)
                                    for y in range(10)
                                ),
                            ),
                        )
                    }
                )
            }
        )
    engine = ActionEngine(foundation.engine.reviewer, foundation.engine.resources, rules)
    play = build_play(path, engine, backend=backend, rng=secrets if seeded else RecordedDice(()))
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        old.world,
        old.resources.model_copy(update={"revision": 0, "events": (), "receipts": ()}),
        tuple(
            ActorSetup(
                actor_id=a.actor_id,
                proposal=a.proposal,
                held_item_hands=(("blade", "right-hand"),) if staff and a.actor_id == "a" else (),
            )
            for a in old.actors
        ),
    )
    if blind:
        state = symptoms(state)
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="encounter",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="forge",
            placements=tuple(
                Placement(actor_id=actor, position=Hex(q=x, r=y), hex_facing=0)
                if hexes
                else Placement(actor_id=actor, position=GridPoint(x=x, y=y))
                for actor, (x, y) in (("a", (1, 3)), ("b", b))
            ),
        ),
        principal_id="gm",
    )
    return cid, play, start.model_copy(update={"expected_revision": 1, "radius": radius})


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hexes", [False, True])
async def test_item_area_roll_uses_nearest_edge_and_power_cost(
    tmp_path: Path, backend: str, hexes: bool
) -> None:
    cid, play, start = await prepare(tmp_path, backend, reduction=1, hexes=hexes)
    play.rng = RecordedDice((4, 4, 4))
    result = await SpellService(play).execute(cid, start, principal_id="a")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)[start.cast_id]
    # Radius 3 at (5,3) includes (3,3): two yards from the caster, not four.
    assert result.checks[0].effective_target == 13
    assert result.outcome == "active" and result.energy_spent == 5
    assert effect.position == (5, 3) and effect.radius == 3 and effect.required_turns == 1
    assert effect.cost == 5 and effect.maintenance == 2
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 5
    assert [
        (h.actor_id, h.spec.damage_dice, h.spec.damage_add)
        for h in state.resources.hazards
        if h.active
    ] == [("b", 1, -1)]
    original = targeting(state.resources, start.cast_id)
    assert original is not None and original.area
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("touching", [False, True])
async def test_blind_item_area_uses_any_touched_part_not_identity_of_named_subject(
    tmp_path: Path, backend: str, touching: bool
) -> None:
    area = AreaSelection(center=(3, 3) if touching else (5, 3))
    cid, play, start = await prepare(tmp_path, backend, area=area, blind=True)
    play.rng = RecordedDice((3, 3, 3))
    result = await SpellService(play).execute(cid, start, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert result.checks[0].effective_target == (15 if touching else 8)
    assert result.outcome == ("active" if touching else "failed")
    assert result.energy_spent == (6 if touching else 1)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (
        4 if touching else 9
    )
    assert bool(state.resources.hazards) == touching


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_empty_center_partial_area_has_only_selected_actual_fire_exposure(
    tmp_path: Path, backend: str
) -> None:
    # B239 permits a partial area; the paid central cell need not itself be affected.
    area = AreaSelection(center=(4, 3), cells=((2, 3), (3, 3)))
    cid, play, start = await prepare(tmp_path, backend, area=area, b=(4, 3))
    play.rng = RecordedDice((4, 4, 4))
    result = await SpellService(play).execute(cid, start, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert result.checks[0].effective_target == 14 and result.energy_spent == 6
    assert latest(state.resources)[start.cast_id].position == (4, 3)
    assert not state.resources.hazards
    play.rng = RecordedDice((6,))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="enter-fire",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=3, y=3),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 7
    assert any(h.actor_id == "b" and h.active for h in state.resources.hazards)


@pytest.mark.parametrize("visible_edge", [False, True])
async def test_hex_area_sight_can_reach_an_edge_when_center_is_occluded(
    tmp_path: Path, visible_edge: bool
) -> None:
    area = AreaSelection(center=(5, 3), cells=((5, 3), (3, 4)) if visible_edge else ((5, 3),))
    cid, play, start = await prepare(tmp_path, "sqlite", area=area, hexes=True, opaque=((3, 3),))
    state = play._load(await play.store.read(cid))
    context = approved_context(play.rules_context, state, start, area_targeting=True)
    assert context.unseen is not visible_edge
    assert context.distance == (3 if visible_edge else 4)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_area_authority_stale_retry_and_seeded_command_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play, start = await prepare(
        tmp_path, backend, area=AreaSelection(center=(5, 3)), seeded=True
    )
    initial = (await play.store.history(cid))[0].state_after
    before = await play.store.read(cid)
    for principal, expected in (("b", AuthorizationError),):
        with pytest.raises(expected):
            await SpellService(play).execute(cid, start, principal_id=principal)
    with pytest.raises(ConflictError):
        await SpellService(play).execute(
            cid, start.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == before
    result = await SpellService(play).execute(cid, start, principal_id="a")
    after = await play.store.read(cid)
    assert await SpellService(play).execute(cid, start, principal_id="a") == result
    assert await play.store.read(cid) == after
    with pytest.raises(ConflictError):
        await SpellService(play).execute(
            cid, start.model_copy(update={"radius": 2}), principal_id="a"
        )
    records = await played(play.store, cid)
    final, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == after


async def test_prior_area_generation_reexecutes_and_live_retry_preserves_original_bytes(
    tmp_path: Path,
) -> None:
    cid, play, start = await prepare(tmp_path, "sqlite", seeded=True)
    initial = (await play.store.history(cid))[0].state_after
    state = play._load(await play.store.read(cid))
    await submit(
        play,
        cid,
        SpellService(play).plan(
            play, member_for(state, "a"), start, principal_id="a", state=state, area_targeting=False
        ),
        principal_id="a",
    )
    after = await play.store.read(cid)
    effect = latest(play._load(after).resources)[start.cast_id]
    assert effect.skill == 11 and effect.area is None
    records = await played(play.store, cid)
    assert "area_targeting_generation" not in command_text(records[-1])
    await SpellService(play).execute(cid, start, principal_id="a")
    assert await play.store.read(cid) == after
    final, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks) and final == after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("blind_onset", [False, True])
async def test_area_completion_keeps_surface_after_anchor_moves_and_rechecks_sight(
    tmp_path: Path, backend: str, blind_onset: bool
) -> None:
    from test_combat_sensory_authority import change

    cid, play, start = await prepare(tmp_path, backend, execution_version=1)
    await SpellService(play).execute(cid, start, principal_id="a")
    before = play._load(await play.store.read(cid))
    accepted = latest(before.resources)[start.cast_id]
    assert accepted.skill == 13 and accepted.position == (5, 3) and accepted.area is not None
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="anchor-moves",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=8, y=3),
        ),
        principal_id="b",
    )
    if blind_onset:
        await change(play, cid, symptoms)
    complete = start.model_copy(
        update={
            "id": "complete",
            "kind": "complete",
            "radius": 1,
            "expected_revision": (await play.store.read(cid))["revision"],
        }
    )
    play.rng = RecordedDice((3, 3, 3))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)[start.cast_id]
    assert result.checks[0].effective_target == (8 if blind_onset else 13)
    assert result.energy_spent == (1 if blind_onset else 6)
    assert effect.position == (5, 3) and effect.radius == 3 and effect.area == accepted.area
    assert effect.cost == accepted.cost == 6 and effect.ready_at == accepted.ready_at == 1
    assert not any(h.active for h in state.resources.hazards)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [1, 2])
async def test_area_minimum_three_refuses_before_any_roll_or_payment(
    tmp_path: Path, backend: str, version: Literal[1, 2]
) -> None:
    from test_combat_sensory_authority import change

    cid, play, start = await prepare(
        tmp_path, backend, radius=1, b=(9, 3), execution_version=version, blind=version == 2
    )
    command = start
    if version == 1:
        await SpellService(play).execute(cid, start, principal_id="a")
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="time",
                actor_id="b",
                expected_revision=2,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
        await change(play, cid, symptoms)
        command = start.model_copy(
            update={
                "id": "complete",
                "kind": "complete",
                "expected_revision": (await play.store.read(cid))["revision"],
            }
        )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="at least 3"):
        await SpellService(play).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before
    assert next(p.current for p in play._load(before).resources.pools if p.id == "fp:a") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_area_cancellation_ends_actual_selected_hazards_without_retargeting(
    tmp_path: Path, backend: str
) -> None:
    cid, play, start = await prepare(tmp_path, backend, area=AreaSelection(center=(5, 3)))
    play.rng = RecordedDice((3, 3, 3))
    await SpellService(play).execute(cid, start, principal_id="a")
    # The other participant acts before the caster can cancel on their turn.
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="other-turn",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    # Cancelling a paid active spell costs 1 FP and retires its actual exposures.
    cancel = start.model_copy(update={"id": "cancel", "kind": "cancel", "expected_revision": 3})
    result = await SpellService(play).execute(cid, cancel, principal_id="a")
    after = await play.store.read(cid)
    state = play._load(after)
    assert result.outcome == "cancelled" and result.energy_spent == 1
    assert latest(state.resources)[start.cast_id].phase == "ended"
    assert not any(h.active for h in state.resources.hazards)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 3
    assert await SpellService(play).execute(cid, cancel, principal_id="a") == result
    assert await play.store.read(cid) == after == await play.store.replay(cid)


@pytest.mark.parametrize(
    "area",
    [
        AreaSelection(center=(0, 0)),
        AreaSelection(center=(5, 3), cells=((1, 3),)),
        AreaSelection(center=(5, 3), height_yards=5),
    ],
)
async def test_area_invalid_extent_is_atomic(tmp_path: Path, area: AreaSelection) -> None:
    cid, play, start = await prepare(tmp_path, "sqlite", area=area)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Area"):
        await SpellService(play).execute(cid, start, principal_id="a")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("authored_area", [False, True])
async def test_area_surface_does_not_use_an_unknown_actor_as_a_location_or_reveal_it(
    tmp_path: Path, authored_area: bool
) -> None:
    from dataclasses import replace

    from test_combat_sensory_authority import change

    from wayfarer.engine.simulation.campaign.access import CampaignMember
    from wayfarer.engine.simulation.events import ResourceChanged, SpellResolved, visible

    cid, play, start = await prepare(
        tmp_path, "sqlite", area=AreaSelection(center=(5, 3)) if authored_area else None
    )
    await change(
        play,
        cid,
        lambda s: s.model_copy(update={"world": replace(s.world, facts=(), knowledge=())}),
    )
    start = start.model_copy(update={"expected_revision": 2})
    before = await play.store.read(cid)
    if not authored_area:
        with pytest.raises(ValidationError, match="anchor is not perceived"):
            await SpellService(play).execute(cid, start, principal_id="a")
        assert await play.store.read(cid) == before
        return
    play.rng = RecordedDice((3, 3, 3))
    await SpellService(play).execute(cid, start, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert "b" not in {e.id for e in state.world.perspective("a").entities}
    private = [
        e.event
        for e in await play.store.stream(cid)
        if isinstance(e.event, SpellResolved)
        or isinstance(e.event, ResourceChanged)
        and e.event.fact.id.startswith("casting-targeting:")
    ]
    assert len(private) >= 2
    for event in private:
        assert visible(event, CampaignMember(principal_id="gm", role="gm"))
        assert not visible(event, CampaignMember(principal_id="b", role="player", actor_ids=("b",)))
        assert not visible(event, CampaignMember(principal_id="observer", role="spectator"))


@pytest.mark.parametrize("current", [False, True])
async def test_selected_area_hazard_generation_preserves_preexisting_command_history(
    tmp_path: Path, current: bool
) -> None:
    cid, play, start = await prepare(
        tmp_path,
        "sqlite",
        area=AreaSelection(center=(3, 3), cells=((3, 3),)),
        b=(3, 3),
        anchor_target=True,
    )
    state = play._load(await play.store.read(cid))
    # Seeded generation replay is covered separately; this oracle checks exact exposure.
    play.rng = RecordedDice((3, 3, 3))
    await submit(
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
    state = play._load(await play.store.read(cid))
    assert {h.actor_id for h in state.resources.hazards if h.active} == (
        {"b"} if current else {"a", "b"}
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("retry", [False, True])
async def test_area_gm_must_keep_current_seat_for_new_commands_and_exact_retries(
    tmp_path: Path, retry: bool
) -> None:
    from test_combat_sensory_authority import change

    cid, play, start = await prepare(tmp_path, "sqlite", area=AreaSelection(center=(5, 3)))
    if retry:
        play.rng = RecordedDice((3, 3, 3))
        await SpellService(play).execute(cid, start, principal_id="gm")
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "members": tuple(
                    m.model_copy(update={"role": "spectator"}) if m.principal_id == "gm" else m
                    for m in state.members
                )
            }
        ),
    )
    before = await play.store.read(cid)
    command = start if retry else start.model_copy(update={"expected_revision": before["revision"]})
    with pytest.raises(ValidationError, match="director authority"):
        await SpellService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("hexes", [False, True])
async def test_current_light_illuminates_a_part_of_the_actual_area(
    tmp_path: Path, hexes: bool
) -> None:
    cid, play, start = await prepare(
        tmp_path, "sqlite", area=AreaSelection(center=(5, 3)), hexes=hexes, darkness=True
    )
    before = play._load(await play.store.read(cid))
    assert approved_context(play.rules_context, before, start, area_targeting=True).unseen
    play.rng = RecordedDice((2, 2, 2))
    await SpellService(play).execute(
        cid,
        SpellCommand(
            id="lamp",
            actor_id="a",
            expected_revision=1,
            kind="start",
            spell_id="light",
            channel_id="illuminate",
            cast_id="lamp",
        ),
        principal_id="a",
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="other-turn",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert not approved_context(play.rules_context, state, start, area_targeting=True).unseen
    play.rng = RecordedDice((3, 3, 3))
    result = await SpellService(play).execute(
        cid, start.model_copy(update={"expected_revision": 3}), principal_id="a"
    )
    assert result.checks[0].effective_target == 12  # Power15 - range2 - one ongoing Light.
    assert result.outcome == "active" and result.energy_spent == 6
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "fp:a"
        )
        == 3
    )
