"""Real Staff combat package opts into corrected declarations under a new pin."""

import secrets
from copy import copy
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_staff_casting import prepare as staff_prepare
from test_statistics import profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.skills.mundane.melee import revise_package_defaults
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    TakeCombatTurn,
)
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands

PROFILE = "gurps-basic-set-4e-2004"
OLD_PACKAGE_DIGEST = "7751299e3e8405796431ca3e967c83c46cbe46f1b49d7fcef9cfc2937fc4a4f0"
OLD_ENGINE_DIGEST = "1c8f6fc47d235d5825d5ef196f8e54a83e019ca842eb0bf232345faf57a776f3"
OLD_TRAINED_BUILD_REVISION = "ca2f0ae31f603bb4e5a6e5374ebe6da6780ccf9f425be55277af5e08d2bba790"
OLD_BUILD_REVISION = "53c98e78a65d27b13a0f1443104b8045536ded371fbf297c35123c7739aeda51"


async def prepare(
    path: Path, backend: str, fresh: bool, *, trained: bool = False
) -> tuple[str, PlayService, Campaign]:
    cid, donor, _ = await staff_prepare(path / "donor", combat=True, distance=1)
    assert donor.engine.digest == OLD_ENGINE_DIGEST
    compiler = donor.engine.reviewer.compiler
    old_package = profile_package(PROFILE)
    old_package = replace(
        old_package,
        definitions=tuple(compiler.definitions.values()),
        sources=tuple(
            {s.id: s for s in (*old_package.sources, *enchantment_package().sources)}.values()
        ),
    )
    assert old_package.digest == compiler.rules.packages[0].digest == OLD_PACKAGE_DIGEST
    original = donor._load(await donor.store.replay(cid, 0))
    actor = next(a for a in original.actors if a.actor_id == "a")
    assert actor.approval and actor.approval.build_revision == OLD_BUILD_REVISION
    selected = revise_package_defaults(old_package, version="2.0.0") if fresh else old_package
    catalog = RulesCatalog((old_package, selected)) if fresh else RulesCatalog((old_package,))
    rules = replace(
        compiler.rules, packages=(PackagePin(selected.id, selected.version, selected.digest),)
    )
    new_compiler = CharacterCompiler(
        catalog,
        rules,
        compiler.policy,
        statistics_profile=PROFILE,
        trait_runtime_hooks=compiler.trait_runtime_hooks,
    )
    reviewer = PowerReviewer(
        new_compiler, donor.engine.reviewer.policy, donor.engine.reviewer.gm_ids
    )
    resources = copy(donor.engine.resources)
    resources.rules = rules
    engine = ActionEngine(reviewer, resources, donor.engine.rules)
    play = build_play(
        path / "actual", engine, backend=backend, rng=secrets, seeds=lambda: f"{2:064x}"
    )
    initial = campaign(engine)
    actors = []
    for current in original.actors:
        proposal = current.proposal
        if trained and current.actor_id == "a":
            proposal = proposal.model_copy(
                update={
                    "draft": proposal.draft.model_copy(
                        update={
                            "purchases": proposal.draft.purchases
                            + (Purchase(definition_id="skill:staff", amount=1),)
                        }
                    )
                }
            )
        actors.append(
            ActorSetup(
                actor_id=current.actor_id,
                proposal=proposal,
                body=current.body,
                held_item_hands=(("blade", "right-hand"),) if current.actor_id == "a" else (),
            )
        )
    state = play.initial_state(
        initial, original.world, original.resources, tuple(actors), members=original.members
    )
    a = next(a for a in state.actors if a.actor_id == "a")
    assert a.approval
    if fresh:
        assert a.approval.build_revision != OLD_BUILD_REVISION
        assert state.configuration_digest != original.configuration_digest
        assert catalog.package(compiler.rules.packages[0]).digest == OLD_PACKAGE_DIGEST
    else:
        assert a.approval.build_revision == (
            OLD_TRAINED_BUILD_REVISION if trained else OLD_BUILD_REVISION
        )
        assert state.configuration_digest == original.configuration_digest == OLD_ENGINE_DIGEST
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    await CombatService(play).execute(
        initial["id"],
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="forge",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=2, y=1)),
            ),
        ),
        principal_id="gm",
    )
    return initial["id"], play, initial


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("fresh", [False, True])
async def test_real_staff_default_uses_explicit_new_pin_without_rewriting_legacy(
    tmp_path: Path, backend: str, fresh: bool
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, fresh)
    if fresh:
        await DefaultKnowledgeService(play).execute(
            cid,
            AdoptDefaultKnowledge(
                id="gm-staff-knowledge",
                actor_id="gm",
                expected_revision=play._load(await play.store.read(cid)).revision,
                performer_id="a",
                known_skill_ids=("skill:staff",),
                reason="The GM confirms Staff is known in this actor's society",
            ),
            principal_id="gm",
        )
    state = play._load(await play.store.read(cid))
    command = TakeCombatTurn(
        id="staff-default",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="attack",
        item_id="blade",
        mode_id="staff-thrust",
        target_id="b",
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    if not fresh:
        with pytest.raises(ValidationError, match="no trained or legal default"):
            await CombatService(play).execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before and play.rng.exhausted()
        return
    await CombatService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 2, 2, 1))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="staff-hit",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert (
        result.injury
        and result.injury.attack.effective_target == 5
        and result.injury.attack.margin == 0
    )
    assert result.injury.basic_damage == 1 and result.injury.injury == 1
    assert play.rng.exhausted()
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "hp:b"
        )
        == 9
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("fresh", [False, True])
async def test_package_revision_and_actual_default_command_reexecute_from_source_genesis(
    tmp_path: Path, backend: str, fresh: bool
) -> None:
    cid, play, initial = await prepare(tmp_path, backend, fresh, trained=not fresh)
    if fresh:
        await DefaultKnowledgeService(play).execute(
            cid,
            AdoptDefaultKnowledge(
                id="gm-staff-knowledge",
                actor_id="gm",
                expected_revision=play._load(await play.store.read(cid)).revision,
                performer_id="a",
                known_skill_ids=("skill:staff",),
                reason="The GM confirms Staff is known in this actor's society",
            ),
            principal_id="gm",
        )
    state = play._load(await play.store.read(cid))
    # The real old package admits trained Staff9. The new package additionally
    # admits untrained Staff5; each command retains its own approved declaration.
    command = TakeCombatTurn(
        id="staff-default",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="attack",
        item_id="blade",
        mode_id="staff-thrust",
        target_id="b",
    )
    await CombatService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    play.seeds = lambda: f"{11:064x}"
    response = ChooseDefense(
        id="resolve",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, response, principal_id="b")
    assert result.injury
    assert result.injury.attack.base_target == (5 if fresh else 9)
    final = await play.store.read(cid)
    assert await CombatService(play).execute(cid, response, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)
