"""Real manufacture, canonical loss/ordinary repair, then Detect checkpoint admission.

The configured generic repair profile exercises the existing repair reducer;
it does not certify Sewing repair rates or add a Play object-damage route.
"""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import support.analyze_magic as manufacture
import test_haste_manufacture as blueprint
from psycopg.conninfo import make_conninfo
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_statistics import gurps_draft, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    RuleDefinition,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.effects import Effect
from wayfarer.engine.rules.magic.protocols import MagicItemBinding
from wayfarer.engine.rules.skills.mundane.arts import definitions
from wayfarer.engine.rules.skills.mundane.social.specialties import CampaignSocialSpecialties
from wayfarer.engine.rules.skills.mundane.specialties import CampaignSkillSpecialties
from wayfarer.engine.rules.skills.mundane.technology.specialties import (
    CampaignTechnologySpecialties,
)
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.objects import DamageObject, StressObject, apply_object
from wayfarer.engine.simulation.equipment.repair_transitions import repair
from wayfarer.engine.simulation.magic.bindings import SpellRules
from wayfarer.engine.simulation.magic.detect_magic_state import (
    DetectSubject,
    ObserveDetectMagicSubject,
)
from wayfarer.engine.simulation.magic.item_state import item_magic_lost
from wayfarer.engine.simulation.resources import Item, ResourceState, Transfer
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError
from wayfarer.orchestration.detect_magic import DetectMagicService
from wayfarer.persistence.postgres import AsyncPostgresStore


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_repaired_real_enchantment_is_refused_before_detection(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Configure physical repair bindings before either authored genesis is seeded.
    profile = ObjectProfile(
        construction="homogenous",
        hp=12,
        dr=1,
        ht=12,
        repair_skill_id="skill:sewing",
        repair_tools_definition="equipment:workshop",
        repair_parts_definition="equipment:workshop",
    )
    sewing = next(d for d in definitions() if d.id == "skill:sewing")
    original_package = profile_package
    original_draft = gurps_draft
    original_copy = EquipmentProfile.model_copy
    original_init = CharacterCompiler.__init__
    original_item = Item

    def item(*, id: str, definition_id: str, owner_id: str) -> Item:
        return original_item(
            id=id,
            definition_id=definition_id,
            owner_id=owner_id,
            quantity=2 if id == "workshop" else 1,
        )

    def compiler_init(
        self: CharacterCompiler,
        catalog: RulesCatalog,
        rules: CampaignRules,
        policy: CampaignPolicy,
        effects: tuple[tuple[str, Effect], ...] = (),
        statistics_profile: str | None = None,
        trait_runtime_hooks: frozenset[str] = frozenset(),
        campaign_skill_specialties: CampaignSkillSpecialties
        | CampaignTechnologySpecialties
        | CampaignSocialSpecialties
        | None = None,
    ) -> None:
        original_init(
            self,
            catalog,
            rules,
            replace(policy, technology_level=2),
            effects,
            statistics_profile,
            trait_runtime_hooks,
            campaign_skill_specialties,
        )

    def package(profile_id: str, *extra: RuleDefinition) -> RulesPackage:
        return original_package(profile_id, *extra, sewing)

    def draft(*purchases: Purchase, st_level: int = 10) -> CharacterDraft:
        return original_draft(
            *purchases,
            Purchase(definition_id="skill:sewing", amount=1, technology_level=2),
            st_level=st_level,
        )

    def copy(
        self: EquipmentProfile, *, update: Mapping[str, object] | None = None, deep: bool = False
    ) -> EquipmentProfile:
        value = original_copy(self, update=update, deep=deep)
        if value.definition_id == "equipment:cloak":
            value = original_copy(value, update={"durability": profile})
        return value

    with monkeypatch.context() as patch:
        patch.setattr(blueprint, "Item", item)
        patch.setattr(CharacterCompiler, "__init__", compiler_init)
        patch.setattr(blueprint, "profile_package", package)
        patch.setattr(manufacture, "profile_package", package)
        patch.setattr(blueprint, "gurps_draft", draft)
        patch.setattr(manufacture, "gurps_draft", draft)
        patch.setattr(EquipmentProfile, "model_copy", copy)
        cid, play, _ = await manufacture.fixture(tmp_path / "manufacture", backend)
    state = play._load(await play.store.read(cid))
    cloak = next(i for i in state.resources.items if i.id == "cloak")
    binding = cloak.enchantments[0]
    assert binding.power == 22
    assert not item_magic_lost(state.resources, cloak.id, binding.id)
    damaged, _ = apply_object(
        play.engine.resources,
        state.resources,
        DamageObject(
            id="hit",
            actor_id="c",
            expected_revision=state.resources.revision,
            item_id="cloak",
            basic_damage=11,
            damage_type="cr",
        ),
        system=True,
    )
    broken, result = apply_object(
        play.engine.resources,
        damaged,
        StressObject(id="break", actor_id="c", expected_revision=damaged.revision, item_id="cloak"),
        system=True,
        rng=RecordedDice((6, 6, 6)),
    )
    assert result.condition.disabled and not result.condition.destroyed
    assert item_magic_lost(broken, cloak.id, binding.id)
    # The workshop remains maker-owned; the test checkpoint places the already
    # manufactured target with its maker for the canonical repair reduction.
    broken = play.engine.resources.apply(
        broken,
        Transfer(
            id="give-maker",
            actor_id="c",
            expected_revision=broken.revision,
            item_id="cloak",
            quantity=1,
            owner_id="a",
        ),
    )
    state = state.model_copy(update={"resources": broken})
    runtime = RulesContext(
        RecordedDice((1, 1, 1, 1)),
        play.engine.resources,
        play.engine.reviewer,
        play.engine.rules,
        None,
    )
    state, task = repair(
        runtime,
        state,
        actor_id="a",
        item_id="cloak",
        command_id="repair",
        stage="start",
        task_id=None,
    )
    state = state.model_copy(update={"revision": state.resources.revision})
    state, _ = play.engine.resolve(
        state,
        Wait(
            id="repair-work",
            actor_id="a",
            expected_revision=state.revision,
            ticks=task.due - state.resources.game_time,
        ),
        clock=play.advance_clock,
    )

    state, finished = repair(
        runtime,
        state,
        actor_id="a",
        item_id="cloak",
        command_id="finish",
        stage="finish",
        task_id=task.id,
    )
    repaired = next(i for i in state.resources.items if i.id == "cloak")
    assert finished.status == "completed"
    assert repaired.condition is not None and not repaired.condition.disabled
    assert repaired.enchantments[0].id == binding.id
    assert repaired.enchantments[0].power == binding.power
    assert item_magic_lost(state.resources, cloak.id, binding.id)
    returned = play.engine.resources.apply(
        state.resources,
        Transfer(
            id="return-reader",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="cloak",
            quantity=1,
            owner_id="c",
        ),
    )
    state = state.model_copy(update={"resources": returned, "revision": returned.revision})
    checkpoint = await play.store.read(cid)
    play.commit(checkpoint, state)
    isolated = None
    schema = "detect_repaired_" + uuid4().hex
    if isinstance(play.store, AsyncPostgresStore):
        async with await psycopg.AsyncConnection.connect(play.store.database_url) as db:
            await db.execute(
                psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema))
            )
        isolated = AsyncPostgresStore(
            make_conninfo(play.store.database_url, options="-csearch_path=" + schema)
        )
    no_dice = RecordedDice(())
    host = build_play(
        tmp_path / "repaired", play.engine, backend=backend, store=isolated, rng=no_dice
    )
    await seed_campaign(host.store, checkpoint)
    saved = await host.store.read(cid)
    command = ObserveDetectMagicSubject(
        id="lost-subject",
        actor_id="gm",
        expected_revision=state.revision,
        subject=DetectSubject(
            id="lost", caster_id="c", target_id="cloak", carrier="inventory", backfire="injury-one"
        ),
    )
    with pytest.raises(ValidationError, match="lost enchantment"):
        await DetectMagicService(host).execute(cid, command, principal_id="gm")
    assert await host.store.read(cid) == saved
    assert no_dice.exhausted()
    if isinstance(play.store, AsyncPostgresStore):
        async with await psycopg.AsyncConnection.connect(play.store.database_url) as db:
            await db.execute(
                psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(schema))
            )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authored_configured_magic_is_not_reported_mundane(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, blueprint_play, _ = await manufacture.fixture(tmp_path / "template", backend)
    template = blueprint_play._load(await blueprint_play.store.read(cid))
    # Author a legacy configured Haste cloak at genesis, not a completed-project
    # binding. It is outside this detector's completed-provenance scope.
    rules = blueprint_play.engine.rules.model_copy(
        update={
            "spells": SpellRules(id="configured", version=1, channels=()).model_copy(
                update={
                    "magic_items": (
                        MagicItemBinding(
                            id="configured-haste", item_id="cloak", spell_id="haste", power=15
                        ),
                    )
                }
            )
        }
    )
    engine = ActionEngine(blueprint_play.engine.reviewer, blueprint_play.engine.resources, rules)
    no_dice = RecordedDice(())
    host = build_play(tmp_path / "configured", engine, backend=backend, rng=no_dice)
    initial = campaign(engine)
    resources = ResourceState(
        owners=template.resources.owners,
        items=tuple(i.model_copy(update={"enchantments": ()}) for i in template.resources.items),
    )
    state = host.initial_state(
        initial,
        template.world,
        resources,
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=a.proposal, body=a.body)
            for a in template.actors
        ),
        members=template.members,
    )
    initial["play_json"] = state.model_dump_json()
    initial = await seed_campaign(host.store, initial)
    saved = await host.store.read(initial["id"])
    command = ObserveDetectMagicSubject(
        id="configured-subject",
        actor_id="gm",
        expected_revision=0,
        subject=DetectSubject(
            id="configured",
            caster_id="c",
            target_id="cloak",
            carrier="inventory",
            backfire="injury-one",
        ),
    )
    with pytest.raises(ValidationError, match="configured magic"):
        await DetectMagicService(host).execute(initial["id"], command, principal_id="gm")
    assert await host.store.read(initial["id"]) == saved
    assert no_dice.exhausted()
