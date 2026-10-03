"""B480–482 real Power projects feed the existing Haste item consumer."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, seed_campaign
from test_actions import campaign
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_power_wearer_haste import prepare as wearer_blueprint
from test_power_wearer_haste import scores
from test_spell_construction import college_fixtures
from test_statistics import gurps_draft, profile_compiler, profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.healing import package as healing_package
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.magic.enchanting import EnchantingRules, EnchantmentRecipe
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    CALENDAR_DAY,
    MAGE_DAY,
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    SettleEnchanting,
)
from wayfarer.engine.simulation.magic.haste_state import (
    DeclareHasteChannel,
    DeclareHasteItem,
    HasteChannel,
    HasteItem,
    HasteMana,
    HasteSwitch,
    ObserveHasteMana,
    SwitchHasteItem,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.projections import ViewRequest, project
from wayfarer.orchestration.views import campaign_view
from wayfarer.persistence.replay import verify_commands


async def prepare(path: Path, backend: str, reduction: int) -> tuple[str, PlayService]:
    # Reuse the lawful found-Haste equipment blueprint, never its Power or ledger.
    cid, original = await wearer_blueprint(path / "blueprint", levels=1)
    foundation = original._load(await original.store.read(cid))
    base = original.engine.reviewer.compiler
    packages = (enchantment_package(), healing_package(), *college_fixtures(10))
    definitions = dict(base.definitions)
    definitions.update({d.id: d for p in packages for d in p.definitions})
    workshop = RuleDefinition(
        "equipment:workshop",
        DefinitionKind.EQUIPMENT,
        "Workshop",
        "sjg:basic-set-characters-4e-2004",
        0,
        ImplementationStatus.IMPLEMENTED,
    )
    definitions[workshop.id] = workshop
    package = profile_package("gurps-basic-set-4e-2004", *definitions.values())
    package = replace(package, definitions=tuple({d.id: d for d in package.definitions}.values()))
    package = replace(
        package, sources=tuple({s.id: s for p in (package, *packages) for s in p.sources}.values())
    )
    catalog = RulesCatalog((package,))
    rebuilt = profile_compiler("gurps-basic-set-4e-2004", package=package)
    policy = replace(
        base.policy,
        point_budget=1000,
        permitted_sources=frozenset(s.id for s in package.sources),
        allowed_equipment=frozenset({"equipment:cloak", workshop.id}),
    )
    compiler = CharacterCompiler(
        catalog, rebuilt.rules, policy, statistics_profile=base.statistics_profile
    )
    combat = original.engine.rules.combat
    assert combat is not None and combat.gurps_equipment is not None
    equipment = combat.gurps_equipment
    workspace = equipment.entries[0].model_copy(
        update={
            "definition_id": workshop.id,
            "slot": None,
            "weight_millipounds": 1000,
            "durability": None,
        }
    )
    equipment = equipment.model_copy(update={"entries": equipment.entries + (workspace,)})
    resource_engine = ResourceEngine(
        foundation.world,
        catalog,
        compiler.rules,
        policy,
        tuple(e.inventory_spec() for e in equipment.entries),
    )
    recipe = EnchantmentRecipe(
        id="source-power",
        spell_id="spell:power",
        effect_id="effect:power",
        method="slow-and-sure",
        energy_required=500 * 2 ** (reduction - 1),
        target_definition_ids=("equipment:cloak",),
        workspace_definition_id=workshop.id,
        runtime_family="power",
        activation="always-on",
        power_reduction=reduction,
    )
    rules = original.engine.rules.model_copy(
        update={
            "enchanting": EnchantingRules(id="power", version=1, recipes=(recipe,)),
            "combat": combat.model_copy(update={"gurps_equipment": equipment}),
        }
    )
    engine = ActionEngine(
        PowerReviewer(compiler, original.engine.reviewer.policy, frozenset({"gm"})),
        resource_engine,
        rules,
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    purchases = (
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=2),
        *(Purchase(definition_id=f"spell:test-college-{i}", amount=1) for i in range(10)),
        *(
            Purchase(definition_id="spell:" + spell, amount=4)
            for spell in ("enchant", "lend-energy", "recover-energy", "power", "haste")
        ),
    )
    draft = gurps_draft(*purchases)
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 16}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    compilation = compiler.compile(draft)
    assert compilation.build is not None, compilation.diagnostics
    cloak = foundation.resources.items[0]
    cloak = cloak.model_copy(
        update={"enchantments": tuple(b for b in cloak.enchantments if b.spell_id == "haste")}
    )
    assert len(cloak.enchantments) == 1 and cloak.enchantments[0].spell_id == "haste"
    state = play.initial_state(
        initial,
        foundation.world,
        ResourceState(
            owners=foundation.resources.owners,
            items=(cloak, Item(id="workshop", definition_id=workshop.id, owner_id="a")),
        ),
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=CharacterProposal(draft=draft), body=a.body)
            for a in foundation.actors
        ),
        members=foundation.members + (CampaignMember(principal_id="watcher", role="spectator"),),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    haste = HasteService(play)
    await haste.execute(
        initial["id"],
        ObserveHasteMana(
            id="mana",
            actor_id="gm",
            expected_revision=0,
            environment=HasteMana(location_id="dock", mana="normal"),
        ),
        principal_id="gm",
    )
    await haste.execute(
        initial["id"],
        DeclareHasteItem(
            id="haste-facts",
            actor_id="gm",
            expected_revision=1,
            item=HasteItem(
                item_id="cloak",
                binding_id="haste-binding",
                definition_id="equipment:cloak",
                levels=1,
                form="clothing",
            ),
        ),
        principal_id="gm",
    )
    assert scores(play, play._load(await play.store.read(initial["id"]))) == (5, 8)
    return initial["id"], play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("reduction", [1, 2])
async def test_completed_power_project_changes_actual_haste_cost_and_state(
    tmp_path: Path, backend: str, reduction: int
) -> None:
    cid, play = await prepare(tmp_path, backend, reduction)
    service = EnchantmentService(play)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, lambda: f"{1:064x}"
    create = CreateEnchantment(
        id="create-power",
        actor_id="a",
        expected_revision=2,
        project_id="power",
        recipe_id="source-power",
        target_item_id="cloak",
        enchanter_ids=("a", "b"),
    )
    await service.execute(cid, create, principal_id="gm")
    await service.execute(
        cid,
        BeginEnchanting(id="begin-power", actor_id="a", expected_revision=3, project_id="power"),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None
    days = (500 * 2 ** (reduction - 1)) // 2
    due = (days - 1) * CALENDAR_DAY + MAGE_DAY
    assert work.start == 0 and work.due == due
    assert scores(play, state) == (5, 8)
    await service.execute(
        cid,
        AdvanceEnchanting(
            id="work-power",
            actor_id="a",
            expected_revision=4,
            project_id="power",
            work_id=work.id,
            to=due,
        ),
        principal_id="gm",
    )
    settle = SettleEnchanting(
        id="settle-power", actor_id="a", expected_revision=5, project_id="power", work_id=work.id
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="trusted"):
        await service.execute(cid, settle, principal_id="alice")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, settle.model_copy(update={"expected_revision": 4}), principal_id="gm"
        )
    assert await play.store.read(cid) == before
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
    history, events = await play.store.history(cid), await play.store.stream(cid)
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await submit(
            failing,
            cid,
            EnchantmentService(failing).plan(
                failing, failing._load(before), settle, principal_id="gm"
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == events
    result = await service.execute(cid, settle, principal_id="gm")
    assert result.status == "completed"
    assert result.check is not None and result.check.effective_target == 17
    state = play._load(await play.store.read(cid))
    cloak = next(i for i in state.resources.items if i.id == "cloak")
    assert len(cloak.enchantments) == 2
    power = cloak.enchantments[1]
    assert (
        power.spell_id,
        power.power_reduction,
        power.project_id,
        power.recipe_id,
        power.runtime_family,
    ) == ("power", reduction, "power", "source-power", "power")
    assert power.power == 17
    assert state.resources.game_time == due
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    if reduction == 2:
        assert scores(play, state) == (6, 9)
        assert any(
            e.spell_id == "haste" and e.cost == 0 and e.expires_at is None
            for e in latest(state.resources).values()
        )
    else:
        assert scores(play, state) == (5, 8)
        await HasteService(play).execute(
            cid,
            DeclareHasteChannel(
                id="item-channel",
                actor_id="gm",
                expected_revision=6,
                channel=HasteChannel(
                    id="paid-haste",
                    actor_id="a",
                    target_id="a",
                    location_id="dock",
                    magic_item_id="cloak",
                ),
            ),
            principal_id="gm",
        )
        start = RuntimeSpellCommand(
            id="paid-start",
            actor_id="a",
            expected_revision=7,
            kind="start",
            spell_id="haste",
            cast_id="paid",
            channel_id="paid-haste",
            energy=1,
        )
        haste = HasteService(play)
        await haste.execute(cid, start, principal_id="alice")
        await play.execute(
            cid,
            Wait(id="cast-second-one", actor_id="a", expected_revision=8, ticks=1),
            principal_id="a",
        )
        await haste.execute(
            cid,
            start.model_copy(
                update={"id": "paid-concentrate", "expected_revision": 9, "kind": "concentrate"}
            ),
            principal_id="alice",
        )
        await play.execute(
            cid,
            Wait(id="cast-second-two", actor_id="a", expected_revision=10, ticks=1),
            principal_id="a",
        )
        await haste.execute(
            cid,
            start.model_copy(
                update={"id": "paid-complete", "expected_revision": 11, "kind": "complete"}
            ),
            principal_id="alice",
        )
        state = play._load(await play.store.read(cid))
        effect = latest(state.resources)["paid"]
        assert effect.cost == 1 and effect.maintenance == 0 and effect.ready_at == due + 2
        assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
        assert scores(play, state) == (6, 9)
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="future", actor_id="a", expected_revision=state.revision, ticks=121),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert scores(play, state) == (6, 9)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (
        9 if reduction == 1 else 10
    )
    if reduction == 2:
        await HasteService(play).execute(
            cid,
            SwitchHasteItem(
                id="off",
                actor_id="a",
                expected_revision=state.revision,
                switch=HasteSwitch(item_id="cloak", binding_id="haste-binding", enabled=False),
            ),
            principal_id="alice",
        )
    else:
        await HasteService(play).execute(
            cid,
            RuntimeSpellCommand(
                id="cancel-paid",
                actor_id="a",
                expected_revision=state.revision,
                kind="cancel",
                spell_id="haste",
                cast_id="paid",
                channel_id="paid-haste",
                energy=1,
            ),
            principal_id="alice",
        )
    state = play._load(await play.store.read(cid))
    assert scores(play, state) == (5, 8)
    assert len(next(i for i in state.resources.items if i.id == "cloak").enchantments) == 2
    final = await play.store.read(cid)
    play.rng = RecordedDice(())
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await EnchantmentService(restarted).execute(cid, settle, principal_id="gm") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    for member in state.members:
        if member.role != "gm":
            assert "enchantment_projects" not in campaign_view(state, member)
            for name in ("campaign", "stream"):
                visible = json.dumps(
                    project(name, ViewRequest(build_runtime(play), state, member)), default=str
                )
                if "a" in member.actor_ids:
                    assert power.id in visible  # Existing own-inventory policy.
                else:
                    assert power.id not in visible and "power_reduction" not in visible
                assert "enchantment:" not in visible
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(check.folded and check.reexecuted for check in checks)
    assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_power_project_preserves_existing_haste_without_free_effect(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, 2)
    initial = await play.store.read(cid)
    original = next(i for i in play._load(initial).resources.items if i.id == "cloak").enchantments
    count = len(await play.store.history(cid))
    # B481 natural 16 fails even when the lower Enchant/effect skill is 17.
    seed = next(f"{n:064x}" for n in range(10000) if _total(f"{n:064x}") == 16)
    play.rng, play.seeds = secrets, lambda: seed
    service = EnchantmentService(play)
    await service.execute(
        cid,
        CreateEnchantment(
            id="create",
            actor_id="a",
            expected_revision=2,
            project_id="power",
            recipe_id="source-power",
            target_item_id="cloak",
            enchanter_ids=("a", "b"),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        BeginEnchanting(id="begin", actor_id="a", expected_revision=3, project_id="power"),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None
    due = 499 * CALENDAR_DAY + MAGE_DAY
    assert work.due == due
    await service.execute(
        cid,
        AdvanceEnchanting(
            id="advance",
            actor_id="a",
            expected_revision=4,
            project_id="power",
            work_id=work.id,
            to=due,
        ),
        principal_id="gm",
    )
    command = SettleEnchanting(
        id="fail", actor_id="a", expected_revision=5, project_id="power", work_id=work.id
    )
    result = await service.execute(cid, command, principal_id="gm")
    assert result.status == "failed"
    assert result.check is not None and result.check.total == 16
    assert result.check.effective_target == 17
    final = await play.store.read(cid)
    state = play._load(final)
    assert next(i for i in state.resources.items if i.id == "cloak").enchantments == original
    assert not any(i.id == "cloak" for i in state.resources.expended_items)
    condition = next(i for i in state.resources.items if i.id == "cloak").condition
    assert condition is not None and condition.hp == 10
    assert scores(play, state) == (5, 8)
    assert state.resources.game_time == due
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await EnchantmentService(restarted).execute(cid, command, principal_id="gm") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "failed-reexecuted"),
    )
    assert len(checks) == 4 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final


def _total(seed: str) -> int:
    rng = SeededRandom(seed)
    return sum(rng.randbelow(6) + 1 for _ in range(3))
