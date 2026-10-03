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
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.equipment.catalog import Armor
from wayfarer.engine.simulation.magic.enchanting import EnchantingRules, EnchantmentRecipe
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    SettleEnchanting,
)
from wayfarer.engine.simulation.magic.haste_manufacture import ObserveHasteManufacture
from wayfarer.engine.simulation.magic.haste_state import (
    DeclareHasteChannel,
    HasteChannel,
    HasteMana,
    ObserveHasteMana,
)
from wayfarer.engine.simulation.magic.haste_state import (
    items as haste_items,
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
from wayfarer.persistence.command_inputs import intent_input
from wayfarer.persistence.replay import verify_commands


async def prepare(
    path: Path, backend: str, levels: int, problem: str | None = None, *, jewelry: bool = False
) -> tuple[str, PlayService]:
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
    target_definition = "equipment:pendant" if jewelry else "equipment:cloak"
    if jewelry:
        definitions[target_definition] = replace(
            definitions["equipment:cloak"], id=target_definition, name="Plain pendant"
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
        allowed_equipment=frozenset({"equipment:cloak", target_definition, workshop.id}),
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
    equipment = equipment.model_copy(
        update={
            "entries": (
                equipment.entries[0].model_copy(
                    update={
                        "armor": Armor(locations=("torso",), dr=1),
                        "definition_id": target_definition,
                        "slot": "neck" if jewelry else equipment.entries[0].slot,
                    }
                ),
                workspace,
            )
        }
    )
    resource_engine = ResourceEngine(
        foundation.world,
        catalog,
        compiler.rules,
        policy,
        tuple(e.inventory_spec() for e in equipment.entries),
    )
    recipe = EnchantmentRecipe(
        id="source-haste",
        spell_id="spell:haste",
        effect_id="effect:haste",
        method="slow-and-sure",
        energy_required=250 * levels,
        target_definition_ids=(target_definition,),
        workspace_definition_id=workshop.id,
    )
    rules = original.engine.rules.model_copy(
        update={
            "enchanting": EnchantingRules(id="power", version=1, recipes=(recipe,)),
            "combat": combat.model_copy(update={"gurps_equipment": equipment}),
        }
    )
    if problem == "plain-wearable":
        equipment = equipment.model_copy(
            update={
                "entries": (
                    equipment.entries[0].model_copy(update={"armor": None}),
                    *equipment.entries[1:],
                )
            }
        )
        rules = rules.model_copy(
            update={"combat": combat.model_copy(update={"gurps_equipment": equipment})}
        )
    elif problem is not None:
        options: dict[str, dict[str, object]] = {
            "energy": {"energy_required": 249},
            "always-on": {"activation": "always-on"},
            "charges": {"maximum_charges": 1},
            "power": {"power_reduction": 1},
            "magery": {"requires_magery": True},
        }
        updates = options[problem]
        rules = rules.model_copy(
            update={
                "enchanting": EnchantingRules(
                    id="bad", version=1, recipes=(recipe.model_copy(update=updates),)
                )
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
    cloak = cloak.model_copy(update={"enchantments": (), "definition_id": target_definition})
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
    assert scores(play, play._load(await play.store.read(initial["id"]))) == (5, 8)
    return initial["id"], play


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def begin_project(
    play: PlayService, cid: str, levels: int, *, legacy: bool = False
) -> SettleEnchanting:
    service = EnchantmentService(play)
    await service.observe_haste_manufacture(
        cid,
        ObserveHasteManufacture(
            id="observe",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            recipe_id="source-haste",
            target_item_id="cloak",
            levels=levels,
        ),
        principal_id="gm",
    )
    create = CreateEnchantment(
        id="create",
        actor_id="a",
        expected_revision=await revision(play, cid),
        project_id="made",
        recipe_id="source-haste",
        target_item_id="cloak",
        enchanter_ids=("a", "b"),
    )
    if legacy:
        before = await play.store.read(cid)
        await submit(
            play,
            cid,
            service.plan(
                play, play._load(before), create, principal_id="gm", haste_manufacture=False
            ),
            principal_id="gm",
        )
    else:
        await service.execute(cid, create, principal_id="gm")
    await service.execute(
        cid,
        BeginEnchanting(
            id="begin", actor_id="a", expected_revision=await revision(play, cid), project_id="made"
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None
    assert work.start == 0 and work.due == (125 * levels - 1) * 86400 + 28800
    await service.execute(
        cid,
        AdvanceEnchanting(
            id="advance",
            actor_id="a",
            expected_revision=state.revision,
            project_id="made",
            work_id=work.id,
            to=work.due,
        ),
        principal_id="gm",
    )
    return SettleEnchanting(
        id="settle",
        actor_id="a",
        expected_revision=await revision(play, cid),
        project_id="made",
        work_id=work.id,
    )


async def paid_cast(play: PlayService, cid: str, levels: int) -> None:
    service = HasteService(play)
    await service.execute(
        cid,
        DeclareHasteChannel(
            id="channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=HasteChannel(
                id="paid-channel",
                actor_id="a",
                target_id="a",
                location_id="dock",
                magic_item_id="cloak",
            ),
        ),
        principal_id="gm",
    )
    start = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="haste",
        cast_id="paid",
        channel_id="paid-channel",
        energy=levels,
    )
    await service.execute(cid, start, principal_id="alice")
    await play.execute(
        cid,
        Wait(id="first", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    await service.execute(
        cid,
        start.model_copy(
            update={
                "id": "concentrate",
                "kind": "concentrate",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    await play.execute(
        cid,
        Wait(id="second", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    await service.execute(
        cid,
        start.model_copy(
            update={
                "id": "complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["paid"]
    assert effect.cost == 2 * levels and effect.maintenance == levels
    assert scores(play, state) == (5 + levels, 8 + levels)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10 - 2 * levels


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("levels", [1, 2, 3])
async def test_manufactured_armor_actual_paid_haste_and_replay(
    tmp_path: Path, backend: str, levels: int
) -> None:
    cid, play = await prepare(tmp_path, backend, levels)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, lambda: f"{1:064x}"
    settle = await begin_project(play, cid, levels)
    outcome = await EnchantmentService(play).execute(cid, settle, principal_id="gm")
    assert (
        outcome.status == "completed"
        and outcome.check is not None
        and outcome.check.effective_target == 17
    )
    state = play._load(await play.store.read(cid))
    binding = next(i for i in state.resources.items if i.id == "cloak").enchantments[0]
    assert (binding.spell_id, binding.runtime_family, binding.project_id, binding.power) == (
        "haste",
        "spell",
        "made",
        17,
    )
    assert not binding.always_on and not binding.requires_magery and binding.power_reduction == 0
    assert (
        len(haste_items(state.resources)) == 1 and haste_items(state.resources)[0].levels == levels
    )
    assert scores(play, state) == (5, 8)
    for member in state.members:
        if member.role == "gm":
            continue
        for name in ("campaign", "stream"):
            visible = json.dumps(
                project(name, ViewRequest(build_runtime(play), state, member)), default=str
            )
            assert "haste-manufacture:" not in visible
            assert "haste-item:" not in visible and "physical_hash" not in visible
            assert "recipe_json" not in visible and "enchantment_projects" not in visible
            if "a" in member.actor_ids:
                assert binding.id in visible
            else:
                assert binding.id not in visible and "power_reduction" not in visible
    await paid_cast(play, cid, levels)
    final = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await EnchantmentService(restarted).execute(cid, settle, principal_id="gm") == outcome
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_capture_absence_and_failed_settlement(
    tmp_path: Path, backend: str, legacy: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, 1)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    if legacy:
        play.rng, play.seeds = secrets, lambda: f"{1:064x}"
    settle = await begin_project(play, cid, 1, legacy=legacy)
    if not legacy:
        play.rng = RecordedDice((6, 5, 5))
    result = await EnchantmentService(play).execute(cid, settle, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert not haste_items(state.resources)
    if legacy:
        binding = next(i for i in state.resources.items if i.id == "cloak").enchantments[0]
        assert binding.runtime_family is None and result.status == "completed"
        record = await play.store.command_input(cid, "create")
        assert (
            record is not None
            and record.text is not None
            and "haste_manufacture_generation" not in record.text
        )
        records = (await play.store.history(cid))[count:]
        ids = {r.command_id for r in records}
        replayed, checks = await verify_commands(
            initial,
            records,
            [e for e in await play.store.stream(cid) if e.command_id in ids],
            configuration_digest=state.configuration_digest,
            execute=FixtureExecutor(play.engine, tmp_path / "legacy-reexecuted"),
        )
        assert checks and all(c.folded and c.reexecuted for c in checks)
        assert replayed == await play.store.read(cid)

    else:
        assert result.status == "failed"
        assert not any(i.id == "cloak" for i in state.resources.items)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_settlement_authority_revision_and_atomic_binding_rollback(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, 1)
    settle = await begin_project(play, cid, 1)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    with pytest.raises(ValidationError, match="trusted"):
        await EnchantmentService(play).execute(cid, settle, principal_id="alice")
    with pytest.raises(ConflictError):
        await EnchantmentService(play).execute(
            cid,
            settle.model_copy(update={"expected_revision": settle.expected_revision - 1}),
            principal_id="gm",
        )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
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
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert not haste_items(play._load(before).resources)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_exact_create_and_observation_retries_preserve_receipts(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, 1)
    settle = await begin_project(play, cid, 1)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    for command_id in ("observe", "create"):
        source = await play.store.command_input(cid, command_id)
        assert source is not None and source.text is not None
        assert (
            await play.store.duplicate(cid, command_id, intent_input(source.text))
            == next(r for r in history if r.command_id == command_id).state_after
        )
        with pytest.raises(ConflictError, match="different input"):
            await play.store.duplicate(cid, command_id, intent_input(source.text) + " ")
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice((3, 3, 3))
    assert (
        await EnchantmentService(play).execute(cid, settle, principal_id="gm")
    ).status == "completed"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "problem", ["plain-wearable", "energy", "always-on", "charges", "power", "magery"]
)
async def test_invalid_manufacture_recipe_and_form_refuse_before_roll(
    tmp_path: Path, backend: str, problem: str
) -> None:
    cid, play = await prepare(tmp_path, backend, 1, problem)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await EnchantmentService(play).observe_haste_manufacture(
            cid,
            ObserveHasteManufacture(
                id="invalid",
                actor_id="gm",
                expected_revision=1,
                recipe_id="source-haste",
                target_item_id="cloak",
                levels=1,
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("change", ["quantity", "definition", "owner", "disabled", "contained"])
async def test_actual_settlement_plan_rechecks_current_physical_candidate_before_rng(
    tmp_path: Path, backend: str, change: str
) -> None:
    # Deliberately changed current candidates are refusal probes, not accepted
    # producer evidence. The observed/project receipts come from actual commands.
    cid, play = await prepare(tmp_path, backend, 1)
    settle = await begin_project(play, cid, 1)
    before = await play.store.read(cid)
    state = play._load(before)
    from wayfarer.engine.rules.types.object import ObjectCondition

    updates: dict[str, dict[str, object]] = {
        "quantity": {"quantity": 2},
        "definition": {"definition_id": "equipment:workshop"},
        "owner": {"owner_id": "b"},
        "disabled": {"condition": ObjectCondition(hp=0, disabled=True)},
        "contained": {"container_id": "workshop"},
    }
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update=updates[change]) if i.id == "cloak" else i
                for i in state.resources.items
            )
        }
    )
    candidate = before.copy()
    candidate["play_json"] = state.model_copy(update={"resources": resources}).model_dump_json()
    play.rng = RecordedDice(())
    plan = EnchantmentService(play).plan(play, state, settle, principal_id="gm")
    with pytest.raises(ValidationError):
        plan.resolve(candidate)
    assert await play.store.read(cid) == before
    assert not haste_items(state.resources)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_observation_authority_and_immutable_source_refuse_without_write(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, 1)
    command = ObserveHasteManufacture(
        id="source",
        actor_id="gm",
        expected_revision=1,
        recipe_id="source-haste",
        target_item_id="cloak",
        levels=1,
    )
    service = EnchantmentService(play)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    for principal in ("alice", "watcher"):
        with pytest.raises(ValidationError):
            await service.observe_haste_manufacture(cid, command, principal_id=principal)
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    await service.observe_haste_manufacture(cid, command, principal_id="gm")
    selected = await play.store.read(cid)
    with pytest.raises(ConflictError, match="cannot be replaced"):
        await service.observe_haste_manufacture(
            cid,
            command.model_copy(update={"id": "replace", "expected_revision": 2}),
            principal_id="gm",
        )
    assert await play.store.read(cid) == selected


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("first_legacy", [False, True])
async def test_existing_haste_cannot_reuse_observation_for_second_project(
    tmp_path: Path, backend: str, first_legacy: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, 1)
    first = await begin_project(play, cid, 1, legacy=first_legacy)
    play.rng = RecordedDice((3, 3, 3) * 3)
    await EnchantmentService(play).execute(cid, first, principal_id="gm")
    if not first_legacy:
        await paid_cast(play, cid, 1)
        state = play._load(await play.store.read(cid))
        assert latest(state.resources)["paid"].phase == "active"
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    second = CreateEnchantment(
        id="create-second",
        actor_id="a",
        expected_revision=await revision(play, cid),
        project_id="second",
        recipe_id="source-haste",
        target_item_id="cloak",
        enchanter_ids=("a", "b"),
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="already has Haste"):
        await EnchantmentService(play).execute(cid, second, principal_id="gm")
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert len(play._load(before).resources.enchantment_projects) == 1
    assert (
        len(next(i for i in play._load(before).resources.items if i.id == "cloak").enchantments)
        == 1
    )

    legacy = second.model_copy(update={"id": "old-create", "project_id": "old-second"})
    await submit(
        play,
        cid,
        EnchantmentService(play).plan(
            play, play._load(before), legacy, principal_id="gm", haste_manufacture=False
        ),
        principal_id="gm",
    )
    legacy_state = play._load(await play.store.read(cid))
    assert len(legacy_state.resources.enchantment_projects) == 2
    recorded = await play.store.command_input(cid, legacy.id)
    assert (
        recorded is not None
        and recorded.text is not None
        and "haste_manufacture_generation" not in recorded.text
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_captured_manufacture_can_abandon_changed_physical_candidate(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.rules.types.object import ObjectCondition
    from wayfarer.engine.simulation.magic.enchanting_transitions import AbandonEnchantment

    cid, play = await prepare(tmp_path, backend, 1)
    await begin_project(play, cid, 1)
    before = await play.store.read(cid)
    state = play._load(before)
    changed = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"condition": ObjectCondition(hp=0, disabled=True)})
                if i.id == "cloak"
                else i
                for i in state.resources.items
            )
        }
    )
    candidate = before.copy()
    candidate["play_json"] = state.model_copy(update={"resources": changed}).model_dump_json()
    command = AbandonEnchantment(
        id="abandon", actor_id="a", expected_revision=state.revision, project_id="made"
    )
    play.rng = RecordedDice(())
    receipt = (
        EnchantmentService(play).plan(play, state, command, principal_id="gm").resolve(candidate)
    )
    assert receipt["outcome"] == "enchantment:abandoned"
    abandoned = play._load(candidate)
    assert abandoned.resources.enchantment_projects[0].status == "abandoned"
    assert abandoned.resources.enchantment_projects[0].active_work is None
    assert not haste_items(abandoned.resources)
    assert await play.store.read(cid) == before
