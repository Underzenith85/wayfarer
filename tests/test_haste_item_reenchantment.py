"""Immutable Haste declarations admit fresh enchantments after permanent loss."""

from pathlib import Path

import pytest
from test_haste_effects import command as spell_command
from test_magic_item_lifecycle import binding
from test_power_wearer_haste import prepare, scores

from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.magic.haste_bindings import approved_context
from wayfarer.engine.simulation.magic.haste_host import apply_host
from wayfarer.engine.simulation.magic.haste_state import (
    DeclareHasteChannel,
    DeclareHasteItem,
    HasteChannel,
    HasteItem,
    items,
)
from wayfarer.engine.simulation.magic.item_state import item_magic_lost
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.haste import HasteService


def declaration(binding_id: str = "fresh-haste") -> DeclareHasteItem:
    return DeclareHasteItem(
        id="fresh-declaration",
        actor_id="gm",
        expected_revision=2,
        item=HasteItem(
            item_id="cloak",
            binding_id=binding_id,
            definition_id="equipment:cloak",
            levels=2,
            form="clothing",
        ),
    )


async def test_fresh_binding_after_breakage_keeps_old_magic_dead(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    old_item = state.resources.items[0]
    broken = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": (
                        old_item.model_copy(
                            update={
                                "condition": ObjectCondition(hp=0, disabled=True),
                            }
                        ),
                    ),
                }
            )
        }
    )
    broken = play.checkpoint(broken, before=state)
    repaired = broken.model_copy(
        update={
            "resources": broken.resources.model_copy(
                update={
                    "items": (
                        old_item.model_copy(
                            update={
                                "enchantments": old_item.enchantments
                                + (
                                    binding("fresh-haste", item_id="cloak", spell="haste"),
                                    binding(
                                        "fresh-power", item_id="cloak", spell="power", reduction=4
                                    ),
                                )
                            }
                        ),
                    ),
                }
            )
        }
    )
    repaired = play.checkpoint(repaired, before=broken)
    assert scores(play, repaired) == (5, 8)
    updated, _ = apply_host(play.rules_context, repaired, declaration())
    updated = play.checkpoint(updated, before=repaired)
    assert scores(play, updated) == (7, 10)
    assert len(items(updated.resources)) == 2
    assert item_magic_lost(updated.resources, "cloak", "haste-binding")
    channeled, _ = apply_host(
        play.rules_context,
        updated,
        DeclareHasteChannel(
            id="fresh-channel",
            actor_id="gm",
            expected_revision=updated.revision,
            channel=HasteChannel(
                id="haste",
                actor_id="a",
                target_id="a",
                location_id="dock",
                magic_item_id="cloak",
            ),
        ),
    )
    context = approved_context(
        play.rules_context,
        channeled,
        spell_command("fresh-start", channeled.revision, "start", levels=2),
    )
    assert context.item_cast and context.item_power_reduction == 4
    with pytest.raises(ConflictError):
        apply_host(play.rules_context, updated, declaration())
    with pytest.raises(ConflictError):
        apply_host(play.rules_context, repaired, declaration("haste-binding"))


@pytest.mark.parametrize("principal", ["alice", "bob"])
async def test_player_cannot_declare_item_and_failure_is_atomic(
    tmp_path: Path,
    principal: str,
) -> None:
    cid, play = await prepare(tmp_path)
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await HasteService(play).execute(cid, declaration(), principal_id=principal)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize(
    "case", ["live", "missing", "wrong-definition", "stack", "hand", "wrong-spell"]
)
async def test_item_construction_rejections(tmp_path: Path, case: str) -> None:
    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    # Construction cases use no prior declaration, isolating the actual validation.
    state = (
        state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "events": tuple(
                            e for e in state.resources.events if not e.id.startswith("haste-item:")
                        ),
                    }
                )
            }
        )
        if case != "live"
        else state
    )
    command = declaration("haste-binding")
    if case == "missing":
        command = command.model_copy(
            update={"item": command.item.model_copy(update={"item_id": "absent"})}
        )
    elif case == "wrong-definition":
        command = command.model_copy(
            update={"item": command.item.model_copy(update={"definition_id": "equipment:other"})}
        )
    elif case in ("stack", "wrong-spell"):
        item = state.resources.items[0]
        updates = (
            {"quantity": 2}
            if case == "stack"
            else {"enchantments": (binding("haste-binding", item_id="cloak", spell="power"),)}
        )
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"items": (item.model_copy(update=updates),)}
                )
            }
        )
    elif case == "hand":
        spec = play.rules_context.resources.specs["equipment:cloak"]
        play.rules_context.resources.specs["equipment:cloak"] = spec.model_copy(
            update={"slot": "hand"}
        )
    with pytest.raises((ConflictError, ValidationError)):
        apply_host(play.rules_context, state, command)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_fresh_declaration_persists_and_retries_after_repair(
    tmp_path: Path,
    backend: str,
) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.simulation.actions import PlayState

    cid, play = await prepare(tmp_path, backend)

    def break_item(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(
                                update={
                                    "condition": ObjectCondition(hp=0, disabled=True),
                                }
                            )
                            for i in state.resources.items
                        ),
                    }
                )
            }
        )

    await change(cid, play, "break", break_item)

    def reenchant(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(
                                update={
                                    "condition": ObjectCondition(hp=10),
                                    "enchantments": i.enchantments
                                    + (
                                        binding("fresh-haste", item_id="cloak", spell="haste"),
                                        binding(
                                            "fresh-power",
                                            item_id="cloak",
                                            spell="power",
                                            reduction=4,
                                        ),
                                    ),
                                }
                            )
                            for i in state.resources.items
                        ),
                    }
                )
            }
        )

    repaired = await change(cid, play, "reenchant", reenchant)
    assert scores(play, repaired) == (5, 8)
    command = declaration().model_copy(update={"expected_revision": repaired.revision})
    service = HasteService(play)
    result = await service.execute(cid, command, principal_id="gm")
    after = await play.store.read(cid)
    assert scores(play, play._load(after)) == (7, 10)
    assert await service.execute(cid, command, principal_id="gm") == result
    assert await play.store.read(cid) == after
