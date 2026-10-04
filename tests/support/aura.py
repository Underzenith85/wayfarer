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
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneExit, SceneRules
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
    subject_mage: bool = True,
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
                    scenes=(
                        Scene(
                            id="dock",
                            version=1,
                            location_id="dock",
                            title="Workshop",
                            exits=(SceneExit(id="to-alley", destination_id="alley"),),
                        ),
                        Scene(id="alley", version=1, location_id="alley", title="Alley"),
                    ),
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
            for s in ("detect-magic", "identify-spell", "analyze-magic", "aura")
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
    subject_draft = foundation.actors[1].proposal.draft
    if not subject_mage:
        subject_draft = subject_draft.model_copy(
            update={
                "purchases": tuple(
                    p
                    for p in subject_draft.purchases
                    if not p.definition_id.startswith("spell:")
                    and p.definition_id not in {"trait:magery", "trait:magery-0"}
                )
            }
        )
    assert compiler.compile(subject_draft).legal
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
                    else a.proposal.model_copy(update={"draft": subject_draft})
                    if a.actor_id == "b"
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


async def observe(
    play: PlayService, cid: str, *, subject: str = "b", secret: bool = False, mage: bool = True
) -> None:
    from wayfarer.engine.simulation.magic.aura_state import (
        AuraSubjectFacts,
        ObserveAuraSubject,
        SecretAuraFact,
    )
    from wayfarer.orchestration.aura import AuraService

    await AuraService(play).execute(
        cid,
        ObserveAuraSubject(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=AuraSubjectFacts(
                id="subject",
                caster_id="c",
                subject_id=subject,
                classification="living-human",
                classification_complete=True,
                personality_complete=True,
                personality="Patient and cautious",
                emotion_complete=True,
                violent_emotion="Anger",
                secrets_complete=True,
                secret_traits=(
                    SecretAuraFact(
                        id="hidden-talent",
                        description="A concealed magical talent",
                        definition_id="trait:magery",
                    ),
                )
                if secret
                else (),
                mage_power="A practiced mage" if mage else None,
            ),
        ),
        principal_id="gm",
    )
