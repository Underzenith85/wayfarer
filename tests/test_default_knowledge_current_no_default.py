"""B344 No Default: actual Karate and Light hosts require proper training."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from support.default_roll_evidence import record_native_default
from support.runtime import build_play, open_store, seed_campaign
from test_actions import campaign
from test_default_knowledge_hosts import adopt
from test_gurps_melee import setup as melee_setup
from test_spell_bindings import command as spell_command
from test_spell_bindings import setup as spell_setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.gurps_skills import definitions
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge, adopted
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.magic.effects import illuminated
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.spells import SpellService

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trained", [False, True])
@pytest.mark.parametrize("known", [False, True])
async def test_real_karate_punch_has_no_untrained_default_even_with_gm_knowledge(
    tmp_path: Path,
    backend: str,
    trained: bool,
    known: bool,
    record_property: Callable[[str, object], None],
) -> None:
    karate = next(
        definition for definition in definitions(PROFILE) if definition.id == "skill:karate"
    )
    cid, play = await melee_setup(
        tmp_path,
        PROFILE,
        human=True,
        unarmed_fixture=True,
        extra_definitions=(karate,),
        extra_purchases=(Purchase(definition_id=karate.id, amount=4),) if trained else (),
    )
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    if known:
        await adopt(play, cid, ("skill:karate",))
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    command = TakeUnarmedTurn(
        id="karate-punch",
        actor_id="a",
        expected_revision=before["revision"],
        encounter_id="fight",
        action="punch",
        target_id="b",
        skill="skill:karate",
        hands=("left-hand",),
        enter_close_combat=True,
    )
    if trained:
        await CombatService(play).execute(cid, command, principal_id="a")
        pending = play._load(await play.store.read(cid)).encounters[0].pending_unarmed
        assert pending and pending.skill == "skill:karate"
    else:
        with pytest.raises(ValidationError, match="no compiled level"):
            await CombatService(play).execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before
    assert play.rng.exhausted()
    if known and not trained:
        record_native_default(
            "basic-b344-native-karate-no-default",
            {"skill": "skill:karate", "paid_points": 0, "gm_society_known": True},
            {
                "admitted": False,
                "state_unchanged": await play.store.read(cid) == before,
                "dice_exhausted": play.rng.exhausted(),
            },
            record_property,
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trained", [False, True])
@pytest.mark.parametrize("known", [False, True])
async def test_native_light_requires_spell_training_not_magery_or_society_metadata(
    tmp_path: Path,
    backend: str,
    trained: bool,
    known: bool,
    record_property: Callable[[str, object], None],
) -> None:
    template_id, template = await spell_setup(tmp_path / "template", backend=backend)
    source = template._load(await template.store.read(template_id))
    play = build_play(tmp_path / "source", template.engine, backend=backend)
    initial = campaign(template.engine)
    actors = tuple(
        ActorSetup(
            actor_id=actor.actor_id,
            proposal=actor.proposal.model_copy(
                update={
                    "draft": actor.proposal.draft.model_copy(
                        update={
                            "purchases": tuple(
                                purchase
                                for purchase in actor.proposal.draft.purchases
                                if not purchase.definition_id.startswith("spell:")
                                or trained
                                and purchase.definition_id == "spell:light"
                            )
                        }
                    )
                }
            ),
        )
        for actor in source.actors
    )
    state = play.initial_state(
        initial, source.world, source.resources, actors, members=source.members
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    if known:
        # Canonical spell definitions are skill nouns, but a real positive GM
        # observation still cannot replace the separate spell-training gate.
        await DefaultKnowledgeService(play).execute(
            cid,
            AdoptDefaultKnowledge(
                id="society-spell-known",
                actor_id="gm",
                expected_revision=state.revision,
                performer_id="a",
                known_skill_ids=("spell:light",),
                reason="The GM establishes cultural knowledge, without granting spell training",
            ),
            principal_id="gm",
        )
    before = await play.store.read(cid)
    state = play._load(before)
    assert adopted(state, "a") is known
    play.rng = RecordedDice(())
    if not trained:
        with pytest.raises(ValidationError, match="not purchased and approved"):
            await SpellService(play).execute(cid, spell_command(state.revision), principal_id="a")
        assert await play.store.read(cid) == before and play.rng.exhausted()
        if known:
            record_native_default(
                "basic-b344-native-spell-no-default",
                {"skill": "spell:light", "paid_points": 0, "gm_society_known": True},
                {
                    "admitted": False,
                    "state_unchanged": await play.store.read(cid) == before,
                    "dice_exhausted": play.rng.exhausted(),
                },
                record_property,
            )
        return
    await SpellService(play).execute(cid, spell_command(state.revision), principal_id="a")
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="casting", actor_id="a", expected_revision=state.revision, ticks=1),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    await SpellService(play).execute(
        cid, spell_command(state.revision, "complete"), principal_id="a"
    )
    state = play._load(await play.store.read(cid))
    assert illuminated(state, "b") and adopted(state, "a") is known and play.rng.exhausted()
