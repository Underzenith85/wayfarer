"""Original genesis, real critical manufacture, third approved analyzing mage."""

import secrets
from dataclasses import replace
from pathlib import Path

from test_actions import campaign
from test_haste_manufacture import begin_project
from test_haste_manufacture import prepare as manufacture_blueprint
from test_statistics import gurps_draft, profile_package

from support.runtime import build_play, build_runtime, seed_campaign
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.knowledge import package
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    AnalyzeSubject,
    CompleteAnalyzeMagic,
    ObserveAnalyzeMagicSubject,
    ReportAnalyzeMagic,
    StartAnalyzeMagic,
    WorkAnalyzeMagic,
)
from wayfarer.engine.simulation.magic.haste_state import HasteMana, ObserveHasteMana
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Transfer
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.orchestration.analyze_magic import AnalyzeMagicService
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.party import PartyCommand
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.size_forms import SizeFormService


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def fixture(path: Path, backend: str) -> tuple[str, PlayService, Campaign]:
    base_id, original = await manufacture_blueprint(path / "blueprint", backend, 1)
    foundation = original._load(await original.store.read(base_id))
    base = original.engine.reviewer.compiler
    definitions = dict(base.definitions)
    definitions.update({d.id: d for d in package().definitions})
    combined = profile_package("gurps-basic-set-4e-2004", *definitions.values())
    combined = replace(
        combined,
        definitions=tuple({d.id: d for d in combined.definitions}.values()),
        sources=tuple(
            {
                s.id: s for p in (combined, package(), enchantment_package()) for s in p.sources
            }.values()
        ),
    )
    catalog = RulesCatalog((combined,))
    compiler = CharacterCompiler(
        catalog,
        replace(base.rules, packages=(PackagePin(combined.id, combined.version, combined.digest),)),
        base.policy,
        statistics_profile=base.statistics_profile,
    )
    world = replace(
        foundation.world,
        entities=foundation.world.entities
        + (Entity("c", EntityKind.ACTOR, "Cora", location_id="dock"),),
    )
    resources = ResourceEngine(
        world,
        catalog,
        compiler.rules,
        compiler.policy,
        tuple(original.engine.resources.specs.values()),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, original.engine.reviewer.policy, frozenset({"gm"})),
        resources,
        original.engine.rules.model_copy(
            update={
                "scenes": SceneRules(
                    id="analysis-workshop",
                    version=1,
                    scenes=(Scene(id="dock", version=1, location_id="dock", title="Workshop"),),
                )
            }
        ),
    )
    play = build_play(path / "host", engine, backend=backend, rng=secrets)
    play.seeds = lambda: f"{1:064x}"
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=1),
        *(
            Purchase(definition_id="spell:" + s, amount=4 if s == "analyze-magic" else 1)
            for s in ("detect-magic", "identify-spell", "analyze-magic")
        ),
    )
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 11}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    assert compiler.compile(draft).legal
    scenario = foundation.resources.model_copy(
        update={
            "revision": 0,
            "events": (),
            "owners": foundation.resources.owners
            + (foundation.resources.owners[0].model_copy(update={"actor_id": "c"}),),
            "pools": foundation.resources.pools
            + tuple(
                p.model_copy(update={"id": p.id.split(":")[0] + ":c"})
                for p in foundation.resources.pools
                if p.id in ("hp:a", "fp:a")
            ),
        }
    )
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world,
        scenario,
        (
            *(
                ActorSetup(actor_id=a.actor_id, proposal=a.proposal, body=a.body)
                for a in foundation.actors
            ),
            ActorSetup(
                actor_id="c",
                proposal=CharacterProposal(draft=draft),
                body=foundation.actors[0].body,
            ),
        ),
        members=foundation.members
        + (CampaignMember(principal_id="cora", role="player", actor_ids=("c",)),),
    )
    initial["play_json"] = state.model_dump_json()
    initial = await seed_campaign(play.store, initial)
    await HasteService(play).execute(
        initial["id"],
        ObserveHasteMana(
            id="mana",
            actor_id="gm",
            expected_revision=0,
            environment=HasteMana(location_id="dock", mana="normal"),
        ),
        principal_id="gm",
    )
    cid = initial["id"]
    settle = await begin_project(play, cid, 1)
    play.seeds = lambda: f"{1119:064x}"
    await EnchantmentService(play).execute(cid, settle, principal_id="gm")
    play.seeds = lambda: f"{1:064x}"
    for operation in ("drop", "retrieve"):
        await SizeFormService(play).retrieve(
            cid,
            WorldGroundCommand(
                id="item-" + operation,
                actor_id="a",
                expected_revision=await revision(play, cid),
                kind=operation,
                item_id="cloak",
            ),
            principal_id="alice",
        )
    state = play._load(await play.store.read(cid))
    await build_runtime(play).submit_json(
        cid,
        PartyCommand(
            id="give-analysis-subject",
            actor_id="a",
            expected_revision=state.revision,
            kind="transfer_item",
            activity_json=Transfer(
                id="inner-give",
                actor_id="a",
                expected_revision=state.resources.revision,
                item_id="cloak",
                quantity=1,
                owner_id="c",
            ).model_dump_json(),
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    return cid, play, initial


async def start(play: PlayService, cid: str, name: str = "analysis") -> StartAnalyzeMagic:
    service = AnalyzeMagicService(play)
    await service.execute(
        cid,
        ObserveAnalyzeMagicSubject(
            id=name + "-subject",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=AnalyzeSubject(id=name + "-physical", caster_id="c", item_id="cloak"),
        ),
        principal_id="gm",
    )
    command = StartAnalyzeMagic(
        id=name + "-start",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id=name,
        subject_id=name + "-physical",
    )
    await service.execute(cid, command, principal_id="cora")
    return command


async def work(play: PlayService, cid: str, seconds: int = 3600, name: str = "analysis") -> None:
    await AnalyzeMagicService(play).execute(
        cid,
        WorkAnalyzeMagic(
            id=name + "-work-" + str(seconds),
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id=name,
            seconds=seconds,
        ),
        principal_id="cora",
    )


async def complete(
    play: PlayService, cid: str, seed: int, name: str = "analysis"
) -> CompleteAnalyzeMagic:
    play.seeds = lambda: f"{seed:064x}"
    command = CompleteAnalyzeMagic(
        id=name + "-complete",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id=name,
    )
    await build_runtime(play).submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
    return command


async def report(
    play: PlayService, cid: str, claimed: int | None = None, name: str = "analysis"
) -> ReportAnalyzeMagic:
    command = ReportAnalyzeMagic(
        id=name + "-report",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        cast_id=name,
        claimed_power=claimed,
    )
    await build_runtime(play).submit_json(cid, command.model_dump(mode="json"), principal_id="gm")
    return command
