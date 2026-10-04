"""Original genesis, real spell producer and independently approved identifying mage."""

import secrets
from dataclasses import replace
from pathlib import Path

from test_actions import campaign
from test_haste_manufacture import prepare as manufacture_blueprint
from test_statistics import gurps_draft, profile_package

from support.runtime import build_play, seed_campaign
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.fire import package as fire_package
from wayfarer.engine.rules.magic.knowledge import package
from wayfarer.engine.rules.magic.movement import package as movement_package
from wayfarer.engine.rules.magic.spell_catalog import projectile_definition
from wayfarer.engine.rules.magic.water import package as water_package
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.magic.bindings import SpellRules
from wayfarer.engine.simulation.magic.haste_state import HasteMana, ObserveHasteMana
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.world import Entity, EntityKind, Fact
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.play import PlayService


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def fixture(
    path: Path,
    backend: str,
    *,
    extra_purchases: tuple[Purchase, ...] = (),
    producer_iq: int | None = None,
    spell_rules: SpellRules | None = None,
) -> tuple[str, PlayService, Campaign]:
    base_id, original = await manufacture_blueprint(path / "blueprint", backend, 1)
    foundation = original._load(await original.store.read(base_id))
    base = original.engine.reviewer.compiler
    definitions = dict(base.definitions)
    definitions.update(
        {
            d.id: d
            for p in (package(), movement_package(), water_package(), fire_package())
            for d in p.definitions
        }
    )
    projectile = projectile_definition()
    definitions[projectile.id] = projectile
    combined = profile_package("gurps-basic-set-4e-2004", *definitions.values())
    combined = replace(
        combined,
        definitions=tuple({d.id: d for d in combined.definitions}.values()),
        sources=tuple(
            {
                s.id: s
                for p in (
                    combined,
                    package(),
                    movement_package(),
                    water_package(),
                    fire_package(),
                    enchantment_package(),
                )
                for s in p.sources
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
        facts=foundation.world.facts
        + (
            Fact("visible-b", "b", "visible", "yes"),
            Fact("visible-chest", "chest", "visible", "yes"),
        ),
        knowledge=foundation.world.knowledge + (("a", "visible-b"), ("a", "visible-chest")),
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
                "spells": spell_rules if spell_rules is not None else original.engine.rules.spells,
                "scenes": SceneRules(
                    id="analysis-workshop",
                    version=1,
                    scenes=(Scene(id="dock", version=1, location_id="dock", title="Workshop"),),
                ),
            }
        ),
    )
    play = build_play(path / "host", engine, backend=backend, rng=secrets)
    play.seeds = lambda: f"{1:064x}"
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=1),
        *(
            Purchase(definition_id="spell:" + s, amount=1)
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
    producer_draft = foundation.actors[0].proposal.draft
    producer_draft = producer_draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": producer_iq})
                if p.definition_id == "attribute:iq" and producer_iq is not None
                else p
                for p in producer_draft.purchases
            )
            + extra_purchases
        }
    )
    assert compiler.compile(producer_draft).legal
    state = play.initial_state(
        initial,
        world,
        scenario,
        (
            *(
                ActorSetup(
                    actor_id=a.actor_id,
                    proposal=a.proposal.model_copy(update={"draft": producer_draft})
                    if a.actor_id == "a"
                    else a.proposal,
                    body=a.body,
                    aware_of=("b",) if a.actor_id == "a" else (),
                )
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
    return initial["id"], play, initial


async def producer(play: PlayService, cid: str, *, finish: bool = True) -> None:
    from wayfarer.engine.simulation.actions import Wait
    from wayfarer.engine.simulation.magic.haste_state import DeclareHasteChannel, HasteChannel
    from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand

    await HasteService(play).execute(
        cid,
        DeclareHasteChannel(
            id="haste-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=HasteChannel(
                id="source-haste", actor_id="a", target_id="b", location_id="dock"
            ),
        ),
        principal_id="gm",
    )

    def command(kind: str) -> RuntimeSpellCommand:
        return RuntimeSpellCommand.model_validate(
            dict(
                id="source-" + kind,
                actor_id="a",
                expected_revision=0,
                kind=kind,
                spell_id="haste",
                channel_id="source-haste",
                cast_id="source",
                energy=1,
            )
        )

    await HasteService(play).execute(
        cid,
        command("start").model_copy(update={"expected_revision": await revision(play, cid)}),
        principal_id="alice",
    )
    if finish:
        from wayfarer.engine.simulation.magic.spell_state import latest

        effect = latest(play._load(await play.store.read(cid)).resources)["source"]
        for second in range(effect.ready_at - effect.started_at):
            await play.execute(
                cid,
                Wait(
                    id="source-second-" + str(second),
                    actor_id="a",
                    expected_revision=await revision(play, cid),
                    ticks=1,
                ),
                principal_id="a",
            )
            operation = (
                "complete" if second == effect.ready_at - effect.started_at - 1 else "concentrate"
            )
            play.seeds = lambda: f"{1:064x}"
            await HasteService(play).execute(
                cid,
                command(operation).model_copy(
                    update={"expected_revision": await revision(play, cid)}
                ),
                principal_id="alice",
            )


async def observe(play: PlayService, cid: str, *, unfamiliar: bool = False) -> None:
    from wayfarer.engine.simulation.magic.identify_spell_state import (
        IdentifySubject,
        ObserveIdentifySpellSubject,
        UnfamiliarSpell,
    )
    from wayfarer.orchestration.identify_spell import IdentifySpellService

    await IdentifySpellService(play).execute(
        cid,
        ObserveIdentifySpellSubject(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=IdentifySubject(
                id="subject",
                caster_id="c",
                subject_id="b",
                unfamiliar=(
                    UnfamiliarSpell(spell_id="haste", description="A movement enhancement"),
                )
                if unfamiliar
                else (),
            ),
        ),
        principal_id="gm",
    )
