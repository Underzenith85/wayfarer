"""Map template ownership: fail-closed references and pinned geometry."""

from pathlib import Path

import pytest
from test_tactical import setup

from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.errors import ValidationError


async def test_missing_template_fails_closed_and_geometry_is_pinned(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    campaign = await play.store.read(cid)
    state = play._load(campaign)
    encounter = state.encounters[0]
    context = encounter.spatial
    assert context.kind == "hex"
    broken = encounter.model_copy(
        update={"spatial_context": context.model_copy(update={"battlefield_id": "missing"})}
    )
    with pytest.raises(ValidationError, match="battlefield|template"):
        play.engine.validate(state.model_copy(update={"encounters": (broken,)}))
    rules = play.engine.rules.combat
    assert rules is not None
    board = play.rules_context.require_hex(encounter)
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine

    modified = board.model_copy(update={"darkness_penalty": -1})
    combat = rules.model_copy(
        update={
            "battlefields": tuple(modified if b.id == board.id else b for b in rules.battlefields)
        }
    )
    engine = ActionEngine(
        play.engine.reviewer,
        play.engine.resources,
        play.engine.rules.model_copy(update={"combat": combat}),
    )
    assert engine.digest != play.engine.digest
    assert any(isinstance(b, HexBattlefield) for b in rules.battlefields)


async def test_hex_template_scene_location_is_checked(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.campaign.encounter_context import bind_scene
    from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules

    cid, play = await setup(tmp_path)
    encounter = play._load(await play.store.read(cid)).encounters[0]
    rules = play.engine.rules.combat
    assert rules is not None
    scenes = SceneRules(
        id="scenes",
        version=1,
        scenes=(
            Scene(id="dock-scene", version=1, location_id="dock", title="Dock"),
            Scene(id="wrong-scene", version=1, location_id="alley", title="Alley"),
        ),
    )
    assert bind_scene(encounter, scenes, rules, "dock-scene").scene_id == "dock-scene"
    with pytest.raises(ValidationError, match="location"):
        bind_scene(encounter, scenes, rules, "wrong-scene")
