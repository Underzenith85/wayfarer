"""Campaigns fourth printing B480-482: irreversible loss and item-wide authority."""

from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_enchanting_projects import setup as enchanting_setup
from test_enchanting_source import settle, start
from test_gurps_melee import attack, choice
from test_gurps_melee import setup as melee_setup
from test_objects import fixture, hit
from test_resources import campaign

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import MagicItemBinding, MagicItemInstance, ManaLevel
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.objects import StressObject, apply_object
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.magic.item_state import (
    checkpoint,
    has_item_magic,
    item_magic_lost,
    item_power_reduction,
    item_requires_magery,
    losses,
    require_power_installation,
    usable_item_enchantment,
)
from wayfarer.engine.simulation.magic.spell_transitions import _approved_magic_item
from wayfarer.engine.simulation.magic.spells import SpellCommand
from wayfarer.engine.simulation.resources import Item, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    CombatService,
    EndEncounter,
    RepairEquipment,
    StartEncounter,
)
from wayfarer.orchestration.resources import ResourceService


def binding(
    identifier: str,
    *,
    item_id: str = "sword",
    spell: str = "light",
    power: int = 20,
    reduction: int = 0,
    mage: bool = False,
    owner: str = "a",
) -> MagicItemInstance:
    passive = spell in ("staff", "power")
    return MagicItemInstance(
        id=identifier,
        item_id=item_id,
        spell_id=spell,
        power=power,
        power_reduction=reduction,
        requires_magery=mage,
        project_id="project:" + identifier,
        recipe_id="recipe:" + identifier,
        effect_id="effect:" + identifier,
        runtime_family=spell if passive else "spell",
        activation="always-on" if passive else "cast",
        always_on=passive,
        owner_id=owner,
        created_at=0,
        method="slow-and-sure",
    )


def with_bindings(resources: ResourceState, *bindings: MagicItemInstance) -> ResourceState:
    return resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(
                    update={
                        "enchantments": item.enchantments
                        + tuple(value for value in bindings if value.item_id == item.id)
                    }
                )
                for item in resources.items
            )
        }
    )


def repair_condition(resources: ResourceState, item_id: str = "sword") -> ResourceState:
    """An intact state for pure loss-boundary tests; live repair is tested below."""
    return resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"condition": ObjectCondition(hp=10)})
                if item.id == item_id
                else item
                for item in resources.items
            )
        }
    )


def test_b480_damage_is_not_breakage_and_repair_does_not_restore_old_magic() -> None:
    engine, resources = fixture()
    old = binding("old-light")
    resources = with_bindings(resources, old)
    resources, _ = apply_object(engine, resources, hit(12), system=True)
    assert usable_item_enchantment(resources, old, "normal")
    assert losses(resources) == ()  # Zero HP alone does not mean broken.
    command = StressObject(id="break", actor_id="a", expected_revision=1, item_id="sword")
    broken, result = apply_object(
        engine, resources, command, system=True, rng=RecordedDice((5, 5, 5))
    )
    assert result.condition.disabled and not result.condition.destroyed
    assert broken.object_results[-1] == result
    assert not usable_item_enchantment(broken, old, "normal")
    restored = ResourceState.model_validate_json(broken.model_dump_json())
    assert apply_object(engine, restored, command, system=True) == (broken, result)
    repaired = checkpoint(repair_condition(restored), before=restored)
    assert not usable_item_enchantment(repaired, old, "normal")
    static = MagicItemBinding(id="legacy", item_id="sword", spell_id="daze", power=20)
    assert not usable_item_enchantment(repaired, static, "normal")
    assert checkpoint(repaired) == repaired
    assert next(item for item in repaired.items if item.id == "sword").enchantments == (old,)

    # Binding identity, not a timestamp, distinguishes a genuine later enchantment.
    fresh = binding("new-light")
    assert fresh.created_at == old.created_at == repaired.game_time
    enchanted = with_bindings(repaired, fresh)
    assert usable_item_enchantment(enchanted, fresh, "normal")
    again, _ = apply_object(
        engine,
        enchanted,
        hit(62, id="destroy", expected_revision=enchanted.revision),
        system=True,
    )
    assert item_magic_lost(again, "sword", fresh.id)
    assert len(losses(again)) == 2
    assert not usable_item_enchantment(repair_condition(again), fresh, "normal")


@pytest.mark.parametrize(
    ("power", "mana", "usable"),
    [
        (19, "low", False),
        (20, "low", True),
        (14, "high", False),
        (15, "normal", True),
        (30, "none", False),
    ],
)
def test_b481_each_enchantment_requires_effective_power(
    power: int, mana: ManaLevel, usable: bool
) -> None:
    value = binding("power-tested", power=power)
    resources = ResourceState(
        items=(Item(id="sword", definition_id="sword", owner_id="a", enchantments=(value,)),)
    )
    assert usable_item_enchantment(resources, value, mana) is usable
    assert not usable_item_enchantment(ResourceState(), value, mana)


@pytest.mark.parametrize(
    ("power", "mana", "expected"),
    [(19, "low", 0), (20, "low", 3), (20, "normal", 3), (20, "high", 3), (20, "none", 0)],
)
def test_b480_power_source_resolves_item_wide_before_single_mana_scaling(
    power: int, mana: ManaLevel, expected: int
) -> None:
    light = binding("light")
    daze = binding("daze", spell="daze")
    power_spell = binding("power", spell="power", power=power, reduction=3)
    resources = ResourceState(
        items=(
            Item(
                id="sword",
                definition_id="sword",
                owner_id="a",
                enchantments=(light, daze, power_spell),
            ),
        )
    )
    assert item_power_reduction(resources, "sword", light, (), mana) == expected
    assert item_power_reduction(resources, "sword", daze, (), mana) == expected
    with pytest.raises(ValidationError, match="unsupported composition"):
        require_power_installation(resources, "sword")


def test_b480_legacy_inline_scope_and_unsupported_power_composition() -> None:
    light = MagicItemBinding(
        id="legacy", item_id="sword", spell_id="light", power=20, power_reduction=2
    )
    daze = MagicItemBinding(id="daze", item_id="sword", spell_id="daze", power=20)
    resources = ResourceState(items=(Item(id="sword", definition_id="sword", owner_id="a"),))
    assert item_power_reduction(resources, "sword", light, (light, daze), "normal") == 2
    assert item_power_reduction(resources, "sword", daze, (light, daze), "normal") == 0
    with pytest.raises(ValidationError, match="unsupported composition"):
        require_power_installation(resources, "sword", (light,))
    power = binding("power", spell="power", reduction=2)
    mixed = with_bindings(resources, power)
    with pytest.raises(ValidationError, match="unsupported composition"):
        item_power_reduction(mixed, "sword", light, (light,), "normal")
    duplicate = with_bindings(mixed, binding("second-power", spell="power", reduction=3))
    with pytest.raises(ValidationError, match="unsupported composition"):
        item_power_reduction(duplicate, "sword", daze, (daze,), "normal")
    broken = checkpoint(
        duplicate.model_copy(
            update={
                "items": tuple(
                    item.model_copy(update={"condition": ObjectCondition(hp=10, disabled=True)})
                    for item in duplicate.items
                )
            }
        )
    )
    repaired = repair_condition(broken)
    require_power_installation(repaired, "sword", (light,))
    assert item_power_reduction(repaired, "sword", daze, (daze, light), "normal") == 0


def test_b482_lost_mage_only_history_does_not_restrict_new_magic() -> None:
    staff = binding("staff", spell="staff", mage=True)
    resources = ResourceState(
        items=(
            Item(
                id="sword",
                definition_id="sword",
                owner_id="a",
                enchantments=(staff,),
                condition=ObjectCondition(hp=10, disabled=True),
            ),
        )
    )
    broken = checkpoint(resources)
    repaired = with_bindings(repair_condition(broken), binding("new-light"))
    assert not item_requires_magery(repaired, "sword")
    assert item_requires_magery(
        with_bindings(repaired, binding("new-staff", spell="staff", mage=True)), "sword"
    )


def test_b480_completed_project_and_binding_survive_loss(tmp_path: Path) -> None:
    engine, runtime, state = enchanting_setup(tmp_path, hold_target=True)
    state, _, _ = settle(runtime, start(runtime, state))
    old = next(item for item in state.resources.items if item.id == "blade").enchantments[0]
    profile = ObjectProfile(construction="homogenous", hp=10, dr=0, ht=12)
    engine.resources.specs["equipment:sword"] = engine.resources.specs[
        "equipment:sword"
    ].model_copy(update={"durability": profile})
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"condition": ObjectCondition(hp=10)})
                if item.id == "blade"
                else item
                for item in state.resources.items
            )
        }
    )
    broken, result = apply_object(
        engine.resources,
        resources,
        hit(60, item_id="blade", expected_revision=resources.revision),
        system=True,
    )
    assert result.condition.destroyed
    assert broken.enchantment_projects == resources.enchantment_projects
    assert next(item for item in broken.items if item.id == "blade").enchantments == (old,)
    engine.validate(state.model_copy(update={"revision": broken.revision, "resources": broken}))
    repaired = repair_condition(broken, "blade")
    assert runtime.rules.spells is not None
    with pytest.raises(ValidationError, match="matching enchantment"):
        _approved_magic_item(
            state.model_copy(update={"resources": repaired}),
            SpellCommand(
                id="cast-lost",
                actor_id="a",
                expected_revision=state.revision,
                kind="start",
                spell_id="light",
                channel_id="item-light",
                cast_id="cast",
            ),
            "blade",
            runtime.rules.spells,
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b480_object_loss_commits_once_with_retry_and_replay(
    tmp_path: Path, backend: str
) -> None:
    engine, resources = fixture()
    old = binding("old-light")
    resources = with_bindings(resources, old)
    store = open_store(tmp_path, backend=backend)
    service = ResourceService(store, engine)
    initial = campaign(engine)
    await service.create(initial, resources)
    cid = initial["id"]
    command = hit(62)
    before = await store.read(cid)
    with pytest.raises(ValidationError, match="authority"):
        await service.execute_object(cid, command, principal_id="a")
    assert await store.read(cid) == before
    result = await service.execute_object(
        cid, command, principal_id="a", system=True, rng=RecordedDice(())
    )
    assert len(losses(result)) == 1
    restarted = ResourceService(store, engine)
    assert (
        await restarted.execute_object(
            cid, command, principal_id="a", system=True, rng=RecordedDice(())
        )
        == result
    )
    with pytest.raises(ConflictError):
        await restarted.execute_object(
            cid, command.model_copy(update={"id": "stale"}), principal_id="a", system=True
        )
    assert await store.read(cid) == await store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b480_direct_combat_breakage_uses_shared_checkpoint(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.combat.battlefield import GridPoint

    cid, original = await melee_setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        critical_breakage="ordinary",
        start_encounter=False,
    )
    initial = await original.store.read(cid)
    state = original._load(initial)
    old = binding("old-light", item_id="sword-a")
    state = state.model_copy(update={"resources": with_bindings(state.resources, old)})
    original.commit(initial, state)
    play = build_play(tmp_path, original.engine, backend=backend, filename="magic-combat.sqlite")
    await seed_campaign(play.store, initial)
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    await attack(cid, play)
    play.rng = RecordedDice((6, 6, 6, 1, 1, 1))
    result = await CombatService(play).execute(cid, choice(), principal_id="b")
    after = play._load(await play.store.read(cid))
    assert len(losses(after.resources)) == 1
    assert not usable_item_enchantment(repair_condition(after.resources, "sword-a"), old, "normal")
    assert any(event.id.startswith("critical-breakage:") for event in after.resources.events)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await CombatService(restarted).execute(cid, choice(), principal_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b480_actual_ordinary_repair_preserves_loss_and_repair_evidence(
    tmp_path: Path, backend: str
) -> None:
    cid, original = await melee_setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(
            construction="homogenous",
            hp=12,
            dr=6,
            ht=12,
            repair_skill_id="skill:armoury",
            repair_tools_definition="equipment:armoury-tools",
        ),
        start_encounter=False,
    )
    initial = await original.store.read(cid)
    state = original._load(initial)
    old = binding("old-light", item_id="sword-b", owner="b")
    resources = with_bindings(state.resources, old)
    resources = resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"condition": ObjectCondition(hp=12, disabled=True)})
                if item.id == "sword-b"
                else item
                for item in resources.items
            )
        }
    )
    state = state.model_copy(update={"resources": resources})
    original.commit(initial, state)
    play = build_play(
        tmp_path,
        original.engine,
        backend=backend,
        filename="magic-repair.sqlite",
        rng=RecordedDice(()),
    )
    await seed_campaign(play.store, initial)
    # This also migrates an intactly recorded, already-broken legacy checkpoint.
    begin = RepairEquipment(
        id="repair",
        actor_id="b",
        expected_revision=0,
        encounter_id="fight",
        item_id="sword-b",
        stage="start",
    )
    # Repair routes require an ended encounter; create/end the ordinary fixture encounter.
    from wayfarer.engine.simulation.combat.battlefield import GridPoint

    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end", actor_id="gm", expected_revision=1, encounter_id="fight", reason="workshop"
        ),
        principal_id="gm",
    )
    await CombatService(play).execute(
        cid, begin.model_copy(update={"expected_revision": 2}), principal_id="b"
    )
    await play.execute(
        cid, Wait(id="work", actor_id="b", expected_revision=3, ticks=1800), principal_id="b"
    )
    play.rng = RecordedDice((3, 3, 3))
    finish = RepairEquipment(
        id="finish",
        actor_id="b",
        expected_revision=4,
        encounter_id="fight",
        item_id="sword-b",
        stage="finish",
        task_id="repair",
    )
    result = await CombatService(play).execute(cid, finish, principal_id="b")
    after = play._load(await play.store.read(cid))
    item = next(item for item in after.resources.items if item.id == "sword-b")
    assert item.condition is not None and not item.condition.disabled
    assert not usable_item_enchantment(after.resources, old, "normal")
    static = MagicItemBinding(id="static", item_id="sword-b", spell_id="light", power=20)
    assert not usable_item_enchantment(after.resources, static, "normal")
    assert tasks(after.resources)[0].status == "completed"
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await CombatService(restarted).execute(cid, finish, principal_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_b480_genuine_reenchantment_activates_its_fresh_binding(tmp_path: Path) -> None:
    engine, runtime, state = enchanting_setup(tmp_path, hold_target=True)
    old = binding("historical-light", item_id="blade", mage=True)
    profile = ObjectProfile(construction="homogenous", hp=10, dr=0, ht=12)
    engine.resources.specs["equipment:sword"] = engine.resources.specs[
        "equipment:sword"
    ].model_copy(update={"durability": profile})
    resources = with_bindings(state.resources, old)
    resources = resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(
                    update={"condition": ObjectCondition(hp=10, disabled=True), "ready": False}
                )
                if item.id == "blade"
                else item
                for item in resources.items
            )
        }
    )
    # Seed the already repaired physical object with the preserved previous loss.
    repaired = repair_condition(checkpoint(resources), "blade")
    state = state.model_copy(update={"resources": repaired})
    state, outcome, command = settle(runtime, start(runtime, state))
    assert outcome.status == "completed"
    engine.validate(state)
    item = next(item for item in state.resources.items if item.id == "blade")
    assert item.enchantments[0] == old
    fresh = item.enchantments[1]
    assert fresh.id == "magic-item:project"
    assert not usable_item_enchantment(state.resources, old, "normal")
    assert usable_item_enchantment(state.resources, fresh, "normal")
    assert not item_requires_magery(state.resources, "blade")
    assert runtime.rules.spells is not None
    selected = _approved_magic_item(
        state,
        SpellCommand(
            id="cast-new",
            actor_id="a",
            expected_revision=state.revision,
            kind="start",
            spell_id="light",
            channel_id="item-light",
            cast_id="cast",
        ),
        "blade",
        runtime.rules.spells,
    )
    assert selected.id == fresh.id
    from wayfarer.engine.simulation.magic.enchanting_transitions import apply_enchantment

    assert apply_enchantment(runtime, state, command, system=True)[0] == state


@pytest.mark.parametrize(
    ("power", "mana", "reduction"), [(19, "low", 0), (20, "low", 3), (20, "high", 3)]
)
def test_b480_approved_cast_uses_other_spell_power_authority(
    tmp_path: Path, power: int, mana: ManaLevel, reduction: int
) -> None:
    _, runtime, state = enchanting_setup(tmp_path, hold_target=True)
    light = binding("light", item_id="blade")
    power_spell = binding("power", item_id="blade", spell="power", power=power, reduction=3)
    state = state.model_copy(
        update={"resources": with_bindings(state.resources, light, power_spell)}
    )
    assert runtime.rules.spells is not None
    rules = runtime.rules.spells.model_copy(
        update={
            "channels": tuple(
                channel.model_copy(update={"mana": mana})
                for channel in runtime.rules.spells.channels
            )
        }
    )
    selected = _approved_magic_item(
        state,
        SpellCommand(
            id="cast",
            actor_id="a",
            expected_revision=0,
            kind="start",
            spell_id="light",
            channel_id="item-light",
            cast_id="cast",
        ),
        "blade",
        rules,
    )
    assert selected.power == 20 and selected.power_reduction == reduction


def test_b480_mundane_object_break_preserves_history_and_static_loss_evidence() -> None:
    engine, resources = fixture()
    damaged, damage = apply_object(engine, resources, hit(12), system=True)
    broken, result = apply_object(
        engine,
        damaged,
        StressObject(id="break", actor_id="a", expected_revision=1, item_id="sword"),
        system=True,
        rng=RecordedDice((5, 5, 5)),
    )
    assert result.condition.disabled and not result.condition.destroyed
    assert tuple(event.id for event in broken.events) == ("object:hit", "object:break")
    assert losses(broken) == ()
    assert checkpoint(broken, before=resources) is broken
    assert broken.object_results == (damage, result)
    # Static campaign magic can be resolved later without having rewritten the
    # original mundane/object-only command with a synthetic magic event.
    static = MagicItemBinding(id="legacy", item_id="sword", spell_id="light", power=20)
    repaired = repair_condition(broken)
    assert item_magic_lost(repaired, "sword", static.id)
    assert not usable_item_enchantment(repaired, static, "normal")
    assert losses(checkpoint(repaired, configured_bindings=(static,))) == ()


@pytest.mark.parametrize("configured", [False, True])
def test_b480_shared_checkpoint_scopes_loss_to_known_magic(
    tmp_path: Path, configured: bool
) -> None:
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine

    engine, _, state = enchanting_setup(tmp_path, hold_target=True)
    assert engine.rules.spells is not None
    static = MagicItemBinding(id="legacy", item_id="blade", spell_id="light", power=20)
    spells = engine.rules.spells.model_copy(update={"magic_items": (static,) if configured else ()})
    engine = ActionEngine(
        engine.reviewer, engine.resources, engine.rules.model_copy(update={"spells": spells})
    )
    play = build_play(tmp_path, engine)
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(
                    update={"condition": ObjectCondition(hp=10, disabled=True), "ready": False}
                )
                if item.id == "blade"
                else item
                for item in state.resources.items
            )
        }
    )
    broken = state.model_copy(update={"resources": resources})
    saved = play.checkpoint(broken, before=state, run_npcs=False)
    assert bool(losses(saved.resources)) is configured
    if not configured:
        assert saved.resources.events == broken.resources.events
    else:
        assert not usable_item_enchantment(
            repair_condition(saved.resources, "blade"), static, "normal"
        )


def test_b480_multiple_legacy_inline_reductions_keep_independent_spell_scope() -> None:
    light = MagicItemBinding(
        id="legacy-light", item_id="sword", spell_id="light", power=20, power_reduction=1
    )
    daze = MagicItemBinding(
        id="legacy-daze", item_id="sword", spell_id="daze", power=20, power_reduction=2
    )
    fire = MagicItemBinding(id="legacy-fire", item_id="sword", spell_id="create-fire", power=20)
    resources = ResourceState(items=(Item(id="sword", definition_id="sword", owner_id="a"),))
    configured = (light, daze, fire)
    assert item_power_reduction(resources, "sword", light, configured, "normal") == 1
    assert item_power_reduction(resources, "sword", daze, configured, "normal") == 2
    assert item_power_reduction(resources, "sword", fire, configured, "normal") == 0
    assert item_power_reduction(resources, "sword", light, configured, "none") == 0
    with pytest.raises(ValidationError, match="unsupported composition"):
        require_power_installation(resources, "sword", configured)
    mixed = with_bindings(resources, binding("new-power", spell="power", reduction=3))
    with pytest.raises(ValidationError, match="unsupported composition"):
        item_power_reduction(mixed, "sword", fire, configured, "normal")


def test_b480_power_target_checks_surviving_magic_including_static_bindings() -> None:
    resources = ResourceState(items=(Item(id="sword", definition_id="sword", owner_id="a"),))
    static = MagicItemBinding(id="legacy", item_id="sword", spell_id="light", power=20)
    assert not has_item_magic(resources, "sword")
    assert has_item_magic(resources, "sword", (static,))
    old = binding("old-light")
    enchanted = with_bindings(resources, old)
    assert has_item_magic(enchanted, "sword")
    broken = enchanted.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"condition": ObjectCondition(hp=10, disabled=True)})
                for item in enchanted.items
            )
        }
    )
    repaired = repair_condition(checkpoint(broken, configured_bindings=(static,)))
    assert not has_item_magic(repaired, "sword", (static,))
    assert has_item_magic(with_bindings(repaired, binding("new-light")), "sword", (static,))
