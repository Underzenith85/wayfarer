"""B480/B482 self-powered Haste using actual item custody and Move/Dodge."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, seed_play
from test_actions import campaign, world
from test_magic_item_lifecycle import binding
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_characters import size_modifier_definition
from wayfarer.engine.rules.magic.movement import package
from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.melee.values import score_defense
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.equipment.catalog import (
    EquipmentCatalog,
    EquipmentProfile,
    Provenance,
)
from wayfarer.engine.simulation.magic.haste_effects import bonus
from wayfarer.engine.simulation.magic.haste_state import (
    DeclareHasteItem,
    HasteItem,
    HasteMana,
    HasteSwitch,
    ObserveHasteMana,
    SwitchHasteItem,
)
from wayfarer.engine.simulation.magic.power_wearer import origins
from wayfarer.engine.simulation.magic.spells import PROFILE, SpellResult
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import (
    Equip,
    Item,
    Owner,
    Pool,
    ResourceState,
    Transfer,
    Unequip,
)
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.play import PlayService


async def prepare(
    path: Path,
    backend: str = "sqlite",
    *,
    reduction: int = 2,
    levels: int = 1,
    mana: ManaLevel = "normal",
    mage: bool = False,
    seeded: bool = False,
    size_modifier: int = 0,
) -> tuple[str, PlayService]:
    spells = package()
    cloak = RuleDefinition(
        "equipment:cloak",
        DefinitionKind.EQUIPMENT,
        "Cloak",
        "sjg:basic-set-characters-4e-2004",
        0,
        ImplementationStatus.IMPLEMENTED,
    )
    base = profile_package(PROFILE, *spells.definitions, cloak, size_modifier_definition())
    base = replace(
        base, sources=tuple({s.id: s for s in (*base.sources, *spells.sources)}.values())
    )
    initial_compiler = profile_compiler(PROFILE, package=base)
    catalog = RulesCatalog((base,))
    compiler = CharacterCompiler(
        catalog,
        initial_compiler.rules,
        replace(
            initial_compiler.policy,
            point_budget=1000,
            skill_ceiling=40,
            allow_supernatural=True,
            allowed_equipment=frozenset({"equipment:cloak"}),
        ),
        statistics_profile=PROFILE,
    )
    equipment = EquipmentCatalog(
        profile_id=PROFILE,
        entries=(
            EquipmentProfile(
                definition_id="equipment:cloak",
                provenance=Provenance(
                    source_id="sjg:basic-set-characters-4e-2004",
                    edition="Fourth Edition, third printing (February 2008)",
                    pages=(288,),
                    errata="none",
                ),
                weight_millipounds=2000,
                price=20,
                technology_level=0,
                slot="body",
                durability=ObjectProfile(construction="homogenous", hp=10, dr=0, ht=12),
            ),
        ),
    )
    authored = world()
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="haste", version=1), frozenset({"gm"})),
        ResourceEngine(
            authored,
            catalog,
            compiler.rules,
            compiler.policy,
            tuple(e.inventory_spec() for e in equipment.entries),
        ),
        ActionRules(
            id="haste",
            version=1,
            maximum_wait=10000,
            combat=CombatRules(
                id="haste",
                version=1,
                battlefields=(Battlefield(id="dock", location_id="dock", width=12, height=12),),
                gurps_equipment=equipment,
            ),
        ),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    enchantments = (
        binding("haste-binding", item_id="cloak", spell="haste", mage=mage),
        binding("power-binding", item_id="cloak", spell="power", reduction=reduction),
    )
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        *(
            (Purchase(definition_id="trait:size-modifier", amount=size_modifier),)
            if size_modifier > 0
            else ()
        ),
    )
    await seed_play(
        play,
        initial,
        authored,
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100000), Owner(actor_id="b", capacity=100000)),
            pools=tuple(
                Pool(id="hp:" + a, current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE))
                for a in ("a", "b")
            ),
            items=(
                Item(
                    id="cloak",
                    definition_id="equipment:cloak",
                    owner_id="a",
                    equipped=True,
                    ready=False,
                    condition=ObjectCondition(hp=10),
                    enchantments=enchantments,
                ),
            ),
        ),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=draft),
                body=HumanBody(anatomy="human"),
            ),
            ActorSetup(
                actor_id="b",
                proposal=CharacterProposal(draft=gurps_draft()),
                body=HumanBody(anatomy="human"),
            ),
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    cid = initial["id"]
    if seeded:
        play = build_play(path, engine, store=play.store, rng=secrets)
    service = HasteService(play)
    await service.execute(
        cid,
        ObserveHasteMana(
            id="mana",
            actor_id="gm",
            expected_revision=0,
            environment=HasteMana(location_id="dock", mana=mana),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        DeclareHasteItem(
            id="item",
            actor_id="gm",
            expected_revision=1,
            item=HasteItem(
                item_id="cloak",
                binding_id="haste-binding",
                definition_id="equipment:cloak",
                levels=levels,
                form="clothing",
            ),
        ),
        principal_id="gm",
    )
    return cid, play


def scores(play: PlayService, state: PlayState, actor_id: str = "a") -> tuple[int, int]:
    participant = Combatant(
        actor_id=actor_id,
        initiative=5,
        initiative_dx=10,
        position=GridPoint(x=0, y=0),
        reach=1,
        movement_allowance=5,
    )
    dodge = score_defense(play.rules_context, state, participant, "dodge", targeted_weapon=False)[0]
    assert dodge is not None
    return movement(play.rules_context, state, actor_id), int(dodge.value)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_zero_cast_wearer_has_real_scores_without_time_energy_or_rolls(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    assert scores(play, state) == (6, 9)
    assert state.resources.game_time == 0
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    assert len(origins(state)) == 1
    await play.execute(
        cid, Wait(id="long", actor_id="a", expected_revision=2, ticks=601), principal_id="a"
    )
    state = play._load(await play.store.read(cid))
    assert scores(play, state) == (6, 9) and len(origins(state)) == 1
    switch = SwitchHasteItem(
        id="off",
        actor_id="a",
        expected_revision=3,
        switch=HasteSwitch(item_id="cloak", binding_id="haste-binding", enabled=False),
    )
    result = await HasteService(play).execute(cid, switch, principal_id="alice")
    state = play._load(await play.store.read(cid))
    assert scores(play, state) == (5, 8)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await HasteService(restarted).execute(cid, switch, principal_id="alice") == result
    await HasteService(restarted).execute(
        cid,
        switch.model_copy(
            update={
                "id": "on",
                "expected_revision": 4,
                "switch": switch.switch.model_copy(update={"enabled": True}),
            }
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    assert scores(play, state) == (6, 9) and len(origins(state)) == 2
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_actual_unequip_transfer_and_equipping_new_owner(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    for command, expected in (
        (Unequip(id="remove", actor_id="a", expected_revision=2, item_id="cloak"), 0),
        (
            Transfer(
                id="give",
                actor_id="a",
                expected_revision=3,
                item_id="cloak",
                quantity=1,
                owner_id="b",
            ),
            0,
        ),
        (Equip(id="wear", actor_id="b", expected_revision=4, item_id="cloak", ready=False), 1),
    ):
        before = state
        resources = play.engine.resources.apply(state.resources, command)
        state = play.checkpoint(
            state.model_copy(update={"revision": resources.revision, "resources": resources}),
            before=before,
        )
        assert bonus(state.resources, "a") == 0
        assert bonus(state.resources, "b") == expected
    assert scores(play, state, "b") == (6, 9)


@pytest.mark.parametrize(
    ("mana", "reduction", "levels", "expected"),
    [
        ("normal", 2, 1, 1),
        ("normal", 5, 3, 0),
        ("normal", 6, 3, 3),
        ("low", 3, 1, 0),
        ("low", 4, 1, 1),
        ("high", 1, 1, 1),
        ("very-high", 3, 3, 3),
        ("none", 6, 3, 0),
    ],
)
async def test_mana_scales_power_once_against_explicit_item_magnitude(
    tmp_path: Path, mana: ManaLevel, reduction: int, levels: int, expected: int
) -> None:
    cid, play = await prepare(tmp_path, reduction=reduction, levels=levels, mana=mana)
    state = play._load(await play.store.read(cid))
    assert bonus(state.resources, "a") == expected
    assert scores(play, state) == (5 + expected, 8 + expected)


async def test_no_mana_suspend_and_return_does_not_repair_broken_magic(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    service = HasteService(play)
    off = ObserveHasteMana(
        id="no-mana",
        actor_id="gm",
        expected_revision=2,
        environment=HasteMana(location_id="dock", mana="none"),
    )
    await service.execute(cid, off, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert bonus(state.resources, "a") == 0
    await service.execute(
        cid,
        off.model_copy(
            update={
                "id": "return",
                "expected_revision": 3,
                "environment": HasteMana(location_id="dock", mana="normal"),
            }
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert scores(play, state) == (6, 9) and len(origins(state)) == 2
    broken = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"condition": ObjectCondition(hp=0, disabled=True)})
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    broken = play.checkpoint(broken, before=state)
    assert bonus(broken.resources, "a") == 0
    repaired = broken.model_copy(
        update={
            "resources": broken.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"condition": ObjectCondition(hp=10)})
                        for i in broken.resources.items
                    )
                }
            )
        }
    )
    repaired = play.checkpoint(repaired, before=broken)
    assert bonus(repaired.resources, "a") == 0


async def test_sleep_preserves_free_cast_effect_but_owner_cannot_operate_asleep(
    tmp_path: Path,
) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.simulation.health.survival import (
        BeginSleep,
        BeginSurvival,
        SurvivalContext,
        begin_sleep,
        begin_survival,
    )
    from wayfarer.errors import ValidationError

    cid, play = await prepare(tmp_path)

    def sleep(state: PlayState) -> PlayState:
        context = SurvivalContext(profile_id=PROFILE, ht=10, will=10)
        resources, _ = begin_survival(
            state.resources,
            BeginSurvival(id="needs", actor_id="a", expected_revision=state.resources.revision),
            context,
            system=True,
        )
        resources, _ = begin_sleep(
            resources,
            BeginSleep(id="nap", actor_id="a", expected_revision=resources.revision, seconds=3600),
            context,
            system=True,
        )
        return state.model_copy(update={"resources": resources})

    asleep = await change(cid, play, "sleep", sleep)
    assert bonus(asleep.resources, "a") == 1
    await play.execute(
        cid, Wait(id="sleep-time", actor_id="b", expected_revision=3, ticks=301), principal_id="b"
    )
    asleep = play._load(await play.store.read(cid))
    assert bonus(asleep.resources, "a") == 1 and len(origins(asleep)) == 1
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="awake"):
        await HasteService(play).execute(
            cid,
            SwitchHasteItem(
                id="asleep-off",
                actor_id="a",
                expected_revision=4,
                switch=HasteSwitch(item_id="cloak", binding_id="haste-binding", enabled=False),
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before


async def test_haste_bonus_precedes_low_hp_fp_halving_and_does_not_restore_a_leg(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.health.injury import Wound, apply_injury

    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    for pools, expected in (({"hp:a"}, (3, 5)), ({"fp:a"}, (3, 5)), ({"hp:a", "fp:a"}, (2, 3))):
        weakened = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 3}) if p.id in pools else p
                            for p in state.resources.pools
                        )
                    }
                )
            }
        )
        assert scores(play, weakened) == expected
    resources, _ = apply_injury(
        state.resources,
        Wound(
            id="leg",
            actor_id="a",
            expected_revision=2,
            basic_damage=6,
            resistance=0,
            damage_type="cr",
            location="left-leg",
        ),
        ht=10,
        system=True,
        rng=RecordedDice((3, 3, 3)),
    )
    crippled = state.model_copy(update={"resources": resources})
    assert bonus(resources, "a") == 1 and movement(play.rules_context, crippled, "a") == 0


async def test_live_authority_approval_and_item_wide_magery(tmp_path: Path) -> None:
    from wayfarer.errors import AuthorizationError, ConflictError, ValidationError

    cid, play = await prepare(tmp_path, mage=True)
    state = play._load(await play.store.read(cid))
    assert bonus(state.resources, "a") == 1
    unknown = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"approval": None}) if a.actor_id == "a" else a
                for a in state.actors
            )
        }
    )
    unknown = play.checkpoint(unknown, before=state)
    assert bonus(unknown.resources, "a") == 0
    moved = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(
                    update={
                        "owner_id": "b",
                        "enchantments": tuple(
                            b.model_copy(update={"owner_id": "b"}) for b in i.enchantments
                        ),
                    }
                )
                for i in state.resources.items
            )
        }
    )
    moved_state = play.checkpoint(state.model_copy(update={"resources": moved}), before=state)
    assert bonus(moved_state.resources, "a") == bonus(moved_state.resources, "b") == 0
    switch = SwitchHasteItem(
        id="off",
        actor_id="a",
        expected_revision=2,
        switch=HasteSwitch(item_id="cloak", binding_id="haste-binding", enabled=False),
    )
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await HasteService(play).execute(cid, switch, principal_id="bob")
    assert await play.store.read(cid) == before
    await HasteService(play).execute(cid, switch, principal_id="alice")
    with pytest.raises(ConflictError):
        await HasteService(play).execute(
            cid, switch.model_copy(update={"id": "stale"}), principal_id="alice"
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_combat_move_uses_worn_haste_and_preserves_retry(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn

    cid, play = await prepare(tmp_path, backend)
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=2,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                Placement(actor_id="b", position=GridPoint(x=10, y=0)),
            ),
        ),
        principal_id="gm",
    )
    move = TakeCombatTurn(
        id="move-six",
        actor_id="a",
        expected_revision=3,
        encounter_id="fight",
        maneuver="move",
        destination=GridPoint(x=6, y=0),
    )
    result = await combat.execute(cid, move, principal_id="a")
    state = play._load(await play.store.read(cid))
    actor = next(p for p in state.encounters[0].participants if p.actor_id == "a")
    assert actor.position == GridPoint(x=6, y=0) and actor.movement_allowance == 6
    assert await combat.execute(cid, move, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_paid_cast_zero_upkeep_requires_awake_wearer_at_expiry(tmp_path: Path) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.simulation.magic.haste_state import DeclareHasteChannel, HasteChannel
    from wayfarer.engine.simulation.magic.power_lifecycle import origins as paid_origins
    from wayfarer.engine.simulation.magic.spell_state import latest
    from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand

    cid, play = await prepare(tmp_path, reduction=1)
    service = HasteService(play)
    await service.execute(
        cid,
        DeclareHasteChannel(
            id="channel",
            actor_id="gm",
            expected_revision=2,
            channel=HasteChannel(
                id="paid", actor_id="a", target_id="a", location_id="dock", magic_item_id="cloak"
            ),
        ),
        principal_id="gm",
    )
    start = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=3,
        kind="start",
        spell_id="haste",
        cast_id="paid",
        channel_id="paid",
    )
    await service.execute(cid, start, principal_id="alice")
    await play.execute(
        cid, Wait(id="one", actor_id="a", expected_revision=4, ticks=1), principal_id="a"
    )
    await service.execute(
        cid,
        start.model_copy(
            update={"id": "concentrate", "expected_revision": 5, "kind": "concentrate"}
        ),
        principal_id="alice",
    )
    await play.execute(
        cid, Wait(id="two", actor_id="a", expected_revision=6, ticks=1), principal_id="a"
    )
    play.rng = RecordedDice((3, 3, 3))
    result = await service.execute(
        cid,
        start.model_copy(update={"id": "complete", "expected_revision": 7, "kind": "complete"}),
        principal_id="alice",
    )
    assert isinstance(result, SpellResult)
    assert result.energy_spent == 1
    state = play._load(await play.store.read(cid))
    assert (
        scores(play, state) == (6, 9)
        and len(paid_origins(state.resources)) == 1
        and not origins(state)
    )
    await play.execute(
        cid, Wait(id="renew", actor_id="a", expected_revision=8, ticks=60), principal_id="a"
    )
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["paid"].expires_at == 122 and scores(play, state) == (6, 9)

    def unconscious(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"conditions": ("unconscious",)})
                    if a.actor_id == "a"
                    else a
                    for a in state.actors
                )
            }
        )

    asleep = await change(cid, play, "unconscious", unconscious)
    assert bonus(asleep.resources, "a") == 1
    await play.execute(
        cid, Wait(id="expiry", actor_id="b", expected_revision=10, ticks=60), principal_id="b"
    )
    state = play._load(await play.store.read(cid))
    assert bonus(state.resources, "a") == 0 and latest(state.resources)["paid"].phase == "ended"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_wearer_commands_seed_reexecute_and_concurrent_retries(
    tmp_path: Path, backend: str
) -> None:
    import asyncio

    from support.runtime import played

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare(tmp_path, backend, seeded=True)
    initial = (await play.store.history(cid))[0].state_after
    switch = SwitchHasteItem(
        id="off",
        actor_id="a",
        expected_revision=2,
        switch=HasteSwitch(item_id="cloak", binding_id="haste-binding", enabled=False),
    )
    results = await asyncio.gather(
        *(HasteService(play).execute(cid, switch, principal_id="alice") for _ in range(2))
    )
    assert results[0] == results[1]
    await HasteService(play).execute(
        cid,
        switch.model_copy(
            update={
                "id": "on",
                "expected_revision": 3,
                "switch": switch.switch.model_copy(update={"enabled": True}),
            }
        ),
        principal_id="alice",
    )
    await play.execute(
        cid, Wait(id="wait", actor_id="a", expected_revision=4, ticks=121), principal_id="a"
    )
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)
    assert scores(play, play._load(final)) == (6, 9)


async def test_actual_dodge_uses_haste_but_committed_defense_retry_stays_fixed(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.orchestration.combat import (
        ChooseDefense,
        CombatService,
        StartEncounter,
        TakeCombatTurn,
        TakeUnarmedTurn,
    )

    cid, play = await prepare(tmp_path)
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=2,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                Placement(actor_id="b", position=GridPoint(x=1, y=0)),
            ),
        ),
        principal_id="gm",
    )
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="wait",
            actor_id="a",
            expected_revision=3,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="a",
    )
    play.rng = RecordedDice((3, 3, 3))
    await combat.execute(
        cid,
        TakeUnarmedTurn(
            id="punch",
            actor_id="b",
            expected_revision=4,
            encounter_id="fight",
            action="punch",
            target_id="a",
            hands=("right-hand",),
            enter_close_combat=True,
        ),
        principal_id="b",
    )
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3))
    defense = ChooseDefense(
        id="dodge", actor_id="a", expected_revision=5, encounter_id="fight", defense="dodge"
    )
    result = await combat.execute(cid, defense, principal_id="a")
    assert result.unarmed is not None
    assert result.unarmed.checks[-1].effective_target == 9
    await HasteService(play).execute(
        cid,
        SwitchHasteItem(
            id="off",
            actor_id="a",
            expected_revision=6,
            switch=HasteSwitch(item_id="cloak", binding_id="haste-binding", enabled=False),
        ),
        principal_id="alice",
    )
    assert scores(play, play._load(await play.store.read(cid))) == (5, 8)
    play.rng = RecordedDice(())
    assert await combat.execute(cid, defense, principal_id="a") == result
