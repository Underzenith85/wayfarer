"""B83-85 form returns retain personal state acquired in the changed body (#757)."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, build_runtime, open_store, played, seed_play
from test_actions import campaign, world
from test_shapeshifting_transformations import form
from test_statistics import gurps_draft, profile_package
from trait_support import trait_compiler

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.magic.protocols import MagicItemInstance
from wayfarer.engine.rules.skills.gurps_skills import definitions
from wayfarer.engine.rules.traits.movement_forms import PROFILE, RUNTIME_HOOKS, package
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import (
    ActionRules,
    ActorSetup,
    CheckRule,
    Inspect,
    PlayState,
    TypedAction,
    Wait,
)
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.campaign.transformations import (
    AttachmentRoute,
    TraitRoute,
    TransformationRecord,
    TransformationRule,
    TransformationRules,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import EquipmentSpec, Item, Owner, ResourceState, Transfer
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration.party import PartyCommand
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.transformations import TransformationService, _authority_state
from wayfarer.persistence.replay import verify_commands

FormKind = Literal["alternate-form", "morph"]
ReturnMode = Literal["timed", "forced"]


async def prepare(path: Path, backend: str, kind: FormKind) -> tuple[str, PlayService]:
    rule, _ = form(kind)
    observation = next(d for d in definitions(PROFILE) if d.id == "skill:observation")
    equipment = RuleDefinition(
        "equipment:keepsake",
        DefinitionKind.EQUIPMENT,
        "Keepsake",
        observation.source_id,
        0,
        ImplementationStatus.IMPLEMENTED,
    )
    credential = replace(equipment, id="equipment:smartgun", name="Smartgun")
    additions = replace(
        package(), definitions=package().definitions + (observation, equipment, credential)
    )
    base = trait_compiler("form-state", PROFILE, additions, hooks=RUNTIME_HOOKS)
    combined = replace(
        profile_package(PROFILE),
        id="package:test-form-state",
        definitions=profile_package(PROFILE).definitions + additions.definitions,
    )
    catalog = RulesCatalog((combined,))
    compiler = CharacterCompiler(
        catalog,
        base.rules,
        replace(base.policy, allowed_equipment=frozenset({equipment.id, credential.id})),
        statistics_profile=PROFILE,
        trait_runtime_hooks=RUNTIME_HOOKS,
    )
    personal_skill = Purchase(definition_id=observation.id, amount=4)
    target = rule.target.model_copy(update={"purchases": rule.target.purchases + (personal_skill,)})
    native = gurps_draft(
        next(p for p in target.purchases if p.definition_id == "advantage:" + kind), personal_skill
    )
    rule = rule.model_copy(
        update={
            "target": target,
            "trait_routes": rule.trait_routes
            + (TraitRoute(definition_id=observation.id, follows="mind"),),
        }
    )
    reducer = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="forms", version=1), frozenset({"gm"})),
        ResourceEngine(
            world(),
            catalog,
            compiler.rules,
            compiler.policy,
            (
                EquipmentSpec(definition_id=equipment.id, unit_weight=1, stackable=False),
                EquipmentSpec(
                    definition_id=credential.id, unit_weight=1, stackable=False, smartgun=True
                ),
            ),
        ),
        ActionRules(
            id="forms",
            version=1,
            checks=(
                CheckRule(
                    id="read-letter",
                    action="inspect",
                    target_id="chest",
                    definition_id=observation.id,
                    package_id=combined.id,
                    package_version=combined.version,
                    modifier=10,
                    reveal_fact_ids=("clue",),
                ),
            ),
            transformations=TransformationRules(id="forms", version=1, transformations=(rule,)),
            scenes=SceneRules(
                id="dock",
                version=1,
                scenes=(Scene(id="dock", version=1, location_id="dock", title="Dock"),),
            ),
            party=PartyRules(id="party", version=1),
        ),
    )
    play = build_play(path, reducer, backend=backend)
    initial = campaign(reducer)
    binding = MagicItemInstance(
        id="keepsake-light",
        item_id="keepsake",
        spell_id="spell:light",
        power=15,
        project_id="prior-enchantment",
        recipe_id="light",
        effect_id="light",
        owner_id="a",
        created_at=0,
        method="slow-and-sure",
    )
    await seed_play(
        play,
        initial,
        world().learn("a", "promise"),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100)),
            items=(
                Item(
                    id="keepsake", definition_id=equipment.id, owner_id="a", enchantments=(binding,)
                ),
                Item(
                    id="credential",
                    definition_id=credential.id,
                    owner_id="b",
                    authorized_actor_ids=("a",),
                ),
                Item(
                    id="other",
                    definition_id=credential.id,
                    owner_id="b",
                    authorized_actor_ids=("b",),
                ),
            ),
        ),
        (
            ActorSetup(
                actor_id="a", proposal=CharacterProposal(draft=native), aware_of=("b", "chest")
            ),
            ActorSetup(actor_id="b", proposal=CharacterProposal(draft=gurps_draft())),
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    return initial["id"], play


async def transform(
    play: PlayService, cid: str, operation: str, **fields: object
) -> TransformationRecord:
    revision = (await play.store.read(cid))["revision"]
    return await TransformationService(play).execute(
        cid,
        dict(operation=operation, id=f"form:{revision}", actor_id="a", expected_revision=revision)
        | fields,
        principal_id="gm",
    )


async def act(
    play: PlayService,
    cid: str,
    command: TypedAction | PartyCommand,
    *,
    principal_id: str = "alice",
) -> PlayState:
    await build_runtime(play).submit_json(
        cid, command.model_dump(mode="json"), principal_id=principal_id
    )
    return play._load(await play.store.read(cid))


async def wait(play: PlayService, cid: str, *, principal_id: str = "alice") -> None:
    revision = (await play.store.read(cid))["revision"]
    result = await act(
        play,
        cid,
        Wait(id=f"wait:{revision}", actor_id="a", expected_revision=revision, ticks=10),
        principal_id=principal_id,
    )
    assert result.last_result is not None and result.last_result.status == "committed"


async def activate(play: PlayService, cid: str) -> TransformationRecord:
    state = play._load(await play.store.read(cid))
    assert state.actors[0].approval is not None
    proposed = await transform(
        play,
        cid,
        "propose",
        rule_id="approved-form",
        expected_build_revision=state.actors[0].approval.build_revision,
    )
    treatment = await transform(
        play, cid, "approve", proposal_id=proposed.proposal_id, reason="Approved racial template"
    )
    assert treatment.status == "treatment"
    await wait(play, cid)
    active = await transform(
        play, cid, "resolve", proposal_id=proposed.proposal_id, resolution="complete"
    )
    assert active.status == "active"
    return active


async def return_command(
    play: PlayService,
    cid: str,
    active: TransformationRecord,
    mode: ReturnMode,
    *,
    principal_id: str = "alice",
) -> dict[str, object]:
    if mode == "timed":
        pending = await transform(
            play, cid, "resolve", proposal_id=active.proposal_id, resolution="reverse"
        )
        assert pending.status == "reverting"
        await wait(play, cid, principal_id=principal_id)
    return {
        "operation": "resolve",
        "id": "return-native",
        "actor_id": "a",
        "expected_revision": (await play.store.read(cid))["revision"],
        "proposal_id": active.proposal_id,
        "resolution": "complete" if mode == "timed" else "force",
        **({"influence": "dispel magic"} if mode == "forced" else {}),
    }


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["alternate-form", "morph"])
@pytest.mark.parametrize("mode", ["timed", "forced"])
async def test_personal_discovery_and_trade_survive_return_restart_and_reexecution(
    tmp_path: Path,
    backend: str,
    kind: FormKind,
    mode: ReturnMode,
) -> None:
    cid, play = await prepare(tmp_path, backend, kind)
    initial = await play.store.read(cid)
    active = await activate(play, cid)
    shifted = play._load(await play.store.read(cid))
    assert shifted.actors[0].proposal == active.target_proposal
    assert ("a", "clue") not in shifted.world.knowledge
    inspected = await act(
        play,
        cid,
        Inspect(
            id="learn-in-form", actor_id="a", expected_revision=shifted.revision, target_id="chest"
        ),
        principal_id="alice",
    )
    assert inspected.last_result is not None and inspected.last_result.status == "committed"
    learned = play._load(await play.store.read(cid))
    assert ("a", "clue") in learned.world.knowledge
    transfer = Transfer(
        id="give-keepsake",
        actor_id="a",
        expected_revision=learned.revision,
        item_id="keepsake",
        quantity=1,
        owner_id="b",
    )
    traded = await act(
        play,
        cid,
        PartyCommand(
            kind="transfer_item",
            id="give-in-form",
            actor_id="a",
            expected_revision=learned.revision,
            activity_json=transfer.model_dump_json(),
        ),
        principal_id="alice",
    )
    given = next(i for i in traded.resources.items if i.id == "keepsake")
    assert given.owner_id == given.enchantments[0].owner_id == "b"
    command = await return_command(play, cid, active, mode)
    # Reopen storage and service before the return commits, then once more before retrying it.
    restarted = build_play(tmp_path, play.engine, store=open_store(tmp_path, backend=backend))
    reverted = await TransformationService(restarted).execute(cid, command, principal_id="gm")
    assert reverted.status == "reverted"
    final = restarted._load(await restarted.store.read(cid))
    assert final.actors[0].proposal == active.source_proposal
    assert final.world.knowledge == learned.world.knowledge
    assert final.resources.items == traded.resources.items
    assert final.members == shifted.members
    saved = await restarted.store.read(cid)
    records, stream = await played(restarted.store, cid), await restarted.store.stream(cid)
    retried = build_play(tmp_path, play.engine, store=open_store(tmp_path, backend=backend))
    service = TransformationService(retried)
    assert await service.execute(cid, command, principal_id="gm") == reverted
    with pytest.raises(ConflictError):
        await service.execute(cid, command | {"id": "stale-return"}, principal_id="gm")
    assert await retried.store.read(cid) == saved == await retried.store.replay(cid)
    assert await played(retried.store, cid) == records
    assert await retried.store.stream(cid) == stream
    replayed, checks = await verify_commands(
        initial,
        records,
        stream,
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == saved


def current_authority(state: PlayState) -> PlayState:
    """A later campaign decision revokes Alice, grants Bob, and changes item credentials."""
    return state.model_copy(
        update={
            "world": state.world.learn("a", "clue").learn("b", "clue"),
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(update={"authorized_actor_ids": ("b",)})
                        if item.id == "credential"
                        else item.model_copy(
                            update={
                                "owner_id": "b",
                                "enchantments": tuple(
                                    binding.model_copy(update={"owner_id": "b"})
                                    for binding in item.enchantments
                                ),
                            }
                        )
                        if item.id == "keepsake"
                        else item
                        for item in state.resources.items
                    )
                }
            ),
            "members": tuple(
                member.model_copy(update={"actor_ids": ()})
                if member.principal_id == "alice"
                else member.model_copy(update={"actor_ids": ("b", "a")})
                if member.principal_id == "bob"
                else member
                for member in state.members
            ),
        }
    )


async def record_authority_change(play: PlayService, cid: str) -> None:
    # These externally administered attachments have no player edit command. Exercise their
    # canonical persisted representation, without inventing a player credential/control API.
    revision = (await play.store.read(cid))["revision"]

    def resolve(campaign: Campaign) -> CommandReceipt:
        updated = current_authority(play._load(campaign))
        play.commit(
            campaign,
            updated.model_copy(
                update={
                    "revision": revision + 1,
                    "resources": updated.resources.model_copy(update={"revision": revision + 1}),
                }
            ),
        )
        return CommandReceipt(action="v1-membership", outcome="current-authority")

    await play.store.commit_turn(
        cid, "current-authority", revision, "current-authority", resolve, actor_id="gm"
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["alternate-form", "morph"])
@pytest.mark.parametrize("mode", ["timed", "forced"])
async def test_return_preserves_current_credentials_and_campaign_control(
    tmp_path: Path,
    backend: str,
    kind: FormKind,
    mode: ReturnMode,
) -> None:
    cid, play = await prepare(tmp_path, backend, kind)
    active = await activate(play, cid)
    await record_authority_change(play, cid)
    changed = play._load(await play.store.read(cid))
    command = await return_command(play, cid, active, mode, principal_id="bob")
    reopened = build_play(tmp_path, play.engine, store=open_store(tmp_path, backend=backend))
    result = await TransformationService(reopened).execute(cid, command, principal_id="gm")
    assert result.status == "reverted"
    final = reopened._load(await reopened.store.read(cid))
    assert final.resources.items == changed.resources.items
    assert final.world.knowledge == changed.world.knowledge
    assert final.members == changed.members
    assert final.actors[0].proposal == active.source_proposal
    saved = await reopened.store.read(cid)
    service = TransformationService(reopened)
    assert await service.execute(cid, command, principal_id="gm") == result
    with pytest.raises(AuthorizationError):
        await act(
            reopened,
            cid,
            Wait(id="revoked-controller", actor_id="a", expected_revision=final.revision, ticks=1),
            principal_id="alice",
        )
    assert await reopened.store.read(cid) == saved == await reopened.store.replay(cid)
    result_action = await act(
        reopened,
        cid,
        Wait(id="current-controller", actor_id="a", expected_revision=final.revision, ticks=1),
        principal_id="bob",
    )
    assert result_action.last_result is not None and result_action.last_result.status == "committed"


@pytest.mark.parametrize("restored", ["inventory", "credentials", "knowledge", "control"])
@pytest.mark.parametrize("follows", ["body", "neither"])
async def test_generic_mixed_routes_restore_only_the_attachment_that_moved(
    tmp_path: Path,
    restored: str,
    follows: Literal["body", "neither"],
) -> None:
    cid, play = await prepare(tmp_path, "sqlite", "alternate-form")
    initial = play._load(await play.store.read(cid))
    active = await activate(play, cid)
    changed = current_authority(play._load(await play.store.read(cid)))
    configured = play.engine.rules.transformations
    assert configured is not None
    original = configured.transformations[0]
    routes = tuple(
        AttachmentRoute(
            kind=route.kind, follows=follows, destination_id="bob" if restored == "control" else "b"
        )
        if route.kind == restored
        else route
        for route in original.attachment_routes
    )
    rule = TransformationRule.model_validate(
        original.model_dump()
        | {
            "kind": "body-modification",
            "source_ref": "B294",
            "native_template_cost": None,
            "target_template_cost": None,
            "attachment_routes": routes,
        }
    )
    routed = _authority_state(initial, rule)
    if restored == "inventory":
        assert next(i for i in routed.resources.items if i.id == "keepsake").owner_id == "b"
    elif restored == "credentials":
        assert (
            "a"
            not in next(
                i for i in routed.resources.items if i.id == "credential"
            ).authorized_actor_ids
        )
    elif restored == "knowledge":
        assert ("a", "promise") not in routed.world.knowledge
    else:
        assert next(m for m in routed.members if m.principal_id == "alice").actor_ids == ()
    reverted = _authority_state(changed, rule, reverse=active)
    keepsake = next(i for i in reverted.resources.items if i.id == "keepsake")
    assert (
        keepsake.owner_id
        == keepsake.enchantments[0].owner_id
        == ("a" if restored == "inventory" else "b")
    )
    credential = next(i for i in reverted.resources.items if i.id == "credential")
    assert credential.authorized_actor_ids == (("a",) if restored == "credentials" else ("b",))
    assert ("a", "promise") in reverted.world.knowledge
    assert (("a", "clue") in reverted.world.knowledge) == (restored != "knowledge")
    assert ("b", "clue") in reverted.world.knowledge
    assert reverted.members == (initial.members if restored == "control" else changed.members)
    assert (
        next(i for i in reverted.resources.items if i.id == "other") == initial.resources.items[-1]
    )
