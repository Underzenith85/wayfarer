"""Actual Rooted Feet producer reaches bounded combat classification refusals."""

from pathlib import Path

import pytest
from support.rooted_feet import cast, fixture, revision

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import StartEncounter
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService


async def start(play: PlayService, cid: str) -> None:
    await CombatService(play).execute(
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


async def execute(play: PlayService, cid: str, actor: str, kind: str, **fields: object) -> None:
    await CombatService(play).execute(
        cid,
        {
            "id": f"combat-{await revision(play, cid)}",
            "actor_id": actor,
            "expected_revision": await revision(play, cid),
            "kind": kind,
            "encounter_id": "fight",
            **fields,
        },
        principal_id=actor,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("attacker", ["a", "b"])
@pytest.mark.parametrize("rooted", [False, True])
async def test_actual_weapon_feint_uses_rooted_weapon_skill_on_each_side(
    tmp_path: Path,
    backend: str,
    attacker: str,
    rooted: bool,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, combat_weapons=True)
    await cast(play, cid, dice=(3, 3, 3, 6, 6, 6) if rooted else (3, 3, 3, 1, 1, 1))
    await start(play, cid)
    if attacker == "b":
        await execute(play, cid, "a", "take_combat_turn", maneuver="do_nothing")
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3) if attacker == "a" else (3, 3, 3, 4, 4, 4))
    await execute(
        play,
        cid,
        attacker,
        "take_combat_turn",
        maneuver="feint",
        item_id="sword-" + attacker,
        target_id="b" if attacker == "a" else "a",
        mode_id="swing",
    )
    campaign = await play.store.read(cid)
    state = play._load(campaign)
    actor = next(p for p in state.encounters[0].participants if p.actor_id == attacker)
    # Purchased Broadsword13 becomes11, while the defender's DX10 fallback remains10.
    expected = (2 if rooted else 0) if attacker == "a" else (1 if rooted else 3)
    assert actor.maneuver_state.feint_penalty == expected
    assert await play.store.replay(cid) == campaign


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("rooted", [False, True])
async def test_actual_grapple_target_composition_refuses_only_active_rooting(
    tmp_path: Path,
    backend: str,
    rooted: bool,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid, dice=(3, 3, 3, 6, 6, 6) if rooted else (3, 3, 3, 1, 1, 1))
    await start(play, cid)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    if rooted:
        with pytest.raises(ValidationError, match="Rooted Feet target control"):
            await execute(
                play,
                cid,
                "a",
                "take_unarmed_turn",
                action="grapple",
                target_id="b",
                hands=("left-hand", "right-hand"),
                enter_close_combat=True,
            )
        assert await play.store.read(cid) == before
        assert await play.store.history(cid) == history
        assert await play.store.stream(cid) == events
    else:
        await execute(
            play,
            cid,
            "a",
            "take_unarmed_turn",
            action="grapple",
            target_id="b",
            hands=("left-hand", "right-hand"),
            enter_close_combat=True,
        )
        play.rng = RecordedDice((3, 3, 3))
        await execute(play, cid, "b", "choose_defense", defense="none")
        state = play._load(await play.store.read(cid))
        assert len(state.encounters[0].grips) == 1
        assert state.encounters[0].grips[0].holder_id == "a"
        assert state.encounters[0].grips[0].target_id == "b"
        assert await play.store.replay(cid) == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_rooted_unarmed_action_refuses_before_entropy(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid)
    await start(play, cid)
    await execute(play, cid, "a", "take_combat_turn", maneuver="do_nothing")
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    events = await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Rooted Feet unarmed action"):
        await execute(
            play,
            cid,
            "b",
            "take_unarmed_turn",
            action="punch",
            target_id="a",
            hands=("right-hand",),
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == events


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_resisted_rooting_retains_ordinary_barehand_parry_offer(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid, dice=(3, 3, 3, 1, 1, 1))
    await start(play, cid)
    await execute(
        play,
        cid,
        "a",
        "take_unarmed_turn",
        action="punch",
        target_id="b",
        hands=("right-hand",),
        enter_close_combat=True,
    )
    pending = play._load(await play.store.read(cid)).encounters[0].pending_unarmed
    assert pending is not None and "parry" in pending.allowed


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unrooted_unarmed_feint_refuses_rooted_mixed_resistance_before_either_roll(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        combat_weapons=True,
        extra_purchases=(Purchase(definition_id="skill:brawling", amount=1),),
    )
    await cast(play, cid)
    await start(play, cid)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Rooted Feet combined unarmed Feint"):
        await execute(
            play,
            cid,
            "a",
            "take_unarmed_turn",
            action="punch",
            target_id="b",
            hands=("left-hand",),
            enter_close_combat=True,
            maneuver="all_out_attack",
            attack_option="feint",
            skill="skill:brawling",
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == events


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("option", ["bare", "acro", "drop"])
async def test_rooted_defense_classification_refuses_before_attack_or_option_dice(
    tmp_path: Path,
    backend: str,
    option: str,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid)
    await start(play, cid)
    await execute(
        play,
        cid,
        "a",
        "take_unarmed_turn",
        action="punch",
        target_id="b",
        hands=("right-hand",),
        enter_close_combat=True,
    )
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    events = await play.store.stream(cid)
    play.rng = RecordedDice(())
    choices: dict[str, object] = {"defense": "dodge"}
    if option == "bare":
        choices = {"defense": "parry", "item_id": "right-hand"}
    else:
        choices["acrobatic_dodge" if option == "acro" else "dodge_and_drop"] = True
    if option == "bare":
        pending = play._load(before).encounters[0].pending_unarmed
        assert pending is not None and "parry" not in pending.allowed
    with pytest.raises(
        ValidationError, match="sensory awareness" if option == "bare" else "Rooted Feet"
    ):
        await execute(play, cid, "b", "choose_defense", **choices)
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == events


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("rooted", [False, True])
async def test_actual_canonical_shield_rush_rooted_target_boundary(
    tmp_path: Path, backend: str, rooted: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, shield_rush_fixture=True)
    await cast(play, cid, dice=(3, 3, 3, 6, 6, 6) if rooted else (3, 3, 3, 1, 1, 1))
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
                Placement(actor_id="b", position=Hex(q=5, r=0), hex_facing=3),
            ),
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    fields = dict(
        maneuver="move_and_attack",
        item_id="rush-shield",
        target_id="b",
        hex_path=tuple(Hex(q=q, r=0) for q in range(1, 6)),
        enter_close_combat=True,
        shield_rush=True,
    )
    if rooted:
        with pytest.raises(ValidationError, match="Rooted Feet"):
            await execute(play, cid, "a", "take_combat_turn", **fields)
        assert await play.store.read(cid) == before
        assert await play.store.history(cid) == history
        assert await play.store.stream(cid) == events
        return
    await execute(play, cid, "a", "take_combat_turn", **fields)
    pending = play._load(await play.store.read(cid)).encounters[0].pending_defense
    assert pending is not None and pending.shield_rush and pending.collision_velocity == 5
    dice = RecordedDice((3, 3, 3, 3, 4, 6, 6, 6))
    play.rng = dice
    await execute(play, cid, "b", "choose_defense", defense="none")
    state = play._load(await play.store.read(cid))
    assert next(pool for pool in state.resources.pools if pool.id == "hp:b").current < 10
    assert dice.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_rooted_plain_unarmed_dodge_remains_admitted(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid)
    await start(play, cid)
    await execute(
        play,
        cid,
        "a",
        "take_unarmed_turn",
        action="punch",
        target_id="b",
        hands=("right-hand",),
        enter_close_combat=True,
    )
    # An ordinary attack roll followed by a failed plain Dodge and real injury.
    play.rng = RecordedDice((2, 2, 2, 6, 6, 6, 6))
    await execute(play, cid, "b", "choose_defense", defense="dodge")
    state = play._load(await play.store.read(cid))
    assert next(pool for pool in state.resources.pools if pool.id == "hp:b").current < 10
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_rooted_dodge_nonzero_modifier_boundary_diagnostic(
    tmp_path: Path, backend: str
) -> None:
    """Canonical guard diagnostic after real cast; not a terrain/visibility host proof."""
    from wayfarer.engine.simulation.combat.unarmed.defense import (
        _require_rooted_dodge_modifiers,
    )

    cid, play, _ = await fixture(tmp_path, backend)
    await cast(play, cid)
    before = await play.store.read(cid)
    state = play._load(before)
    _require_rooted_dodge_modifiers(state, "b", 0, 0)
    for height, external in ((1, 0), (-1, 0), (0, -2), (0, 1)):
        with pytest.raises(ValidationError, match="Rooted Feet unarmed Dodge"):
            _require_rooted_dodge_modifiers(state, "b", height, external)
        _require_rooted_dodge_modifiers(state, "a", height, external)
    assert await play.store.read(cid) == before
