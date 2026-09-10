"""B360-361 lasting consequences: owner proposals, exact GM approval and replay."""

from pathlib import Path

import pytest
from test_mundane_trait_runtime import prepare as prepare_traits
from test_social_dispatch import prepare

from wayfarer.character.compiler import Purchase
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.fright import FrightEffect
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.fright import TimedFright, effects, public_id, save
from wayfarer.simulation.mechanics.gurps_melee import build


async def install(cid: str, play: PlayService, effect: FrightEffect) -> TimedFright:
    state = play._load(await play.store.read(cid))
    item = TimedFright(
        id="private-occurrence",
        actor_id="a",
        trigger_id="secret-monster",
        effect=effect,
        started=0,
        due=60 if effect.condition != "none" else None,
        active=effect.condition != "none",
        recovery_target=10,
    )

    def reduce(campaign: Campaign) -> Event:
        revision = state.revision + 1
        updated = state.model_copy(
            update={
                "revision": revision,
                "resources": save(state.resources, item, "fixture").model_copy(
                    update={"revision": revision}
                ),
            }
        )
        campaign["revision"], campaign["play_json"] = revision, updated.model_dump_json()
        return Event(input="fixture", action="npc", outcome="consequence", roll=None)

    await play.store.commit_turn(cid, "fixture", state.revision, "fixture", reduce, actor_id="gm")
    return item


def proposal(
    play: PlayService, state: PlayState, item: TimedFright, purchases: tuple[Purchase, ...]
) -> dict[str, object]:
    return {
        "id": "propose",
        "actor_id": "a",
        "kind": "propose_fright_build",
        "expected_revision": state.revision,
        "expected_build_revision": build(play.rules_context, state, "a").revision,
        "fright_id": public_id(item),
        "draft": state.actors[0]
        .proposal.draft.model_copy(update={"purchases": purchases})
        .model_dump(mode="json"),
        "reason": "Consequence of the frightening encounter",
    }


@pytest.mark.parametrize("attribute", ["ht", "iq"])
async def test_permanent_loss_approval_is_exact_owner_scoped_and_restart_safe(
    tmp_path: Path, attribute: str
) -> None:
    cid, play = await prepare(tmp_path)
    item = await install(
        cid,
        play,
        FrightEffect(
            table_total=31 if attribute == "ht" else 40,
            permanent_ht_loss=int(attribute == "ht"),
            permanent_iq_loss=int(attribute == "iq"),
        ),
    )
    initial = play._load(await play.store.read(cid))
    old = build(play.rules_context, initial, "a")
    purchases = tuple(
        p.model_copy(update={"amount": 9}) if p.definition_id == "attribute:" + attribute else p
        for p in initial.actors[0].proposal.draft.purchases
    )
    command = proposal(play, initial, item, purchases)
    access = CampaignAccess(play)
    result = await access.execute(cid, command, principal_id="alice")
    assert isinstance(result["fright"], tuple)
    assert build(play.rules_context, play._load(await play.store.read(cid)), "a") == old
    assert "secret-monster" not in str(result) and "private-occurrence" not in str(result)
    assert isinstance(result["fright"], tuple)
    projected = result["fright"][0]
    assert projected["proposal_id"] and projected["build_approval_required"]
    state = play._load(await play.store.read(cid))
    assert (
        CampaignAccess._projection(state, CampaignMember(principal_id="watch", role="spectator"))[
            "fright"
        ]
        == ()
    )
    approval = {
        "id": "approve",
        "kind": "approve_fright_build",
        "actor_id": "a",
        "expected_revision": state.revision,
        "fright_id": public_id(item),
        "proposal_id": projected["proposal_id"],
        "reason": "Reviewed exact loss",
    }
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director authority"):
        await access.execute(cid, approval, principal_id="alice")
    with pytest.raises(ConflictError, match="stale"):
        await access.execute(cid, approval | {"proposal_id": "obsolete"}, principal_id="gm")
    assert await play.store.read(cid) == saved
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "social.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    access = CampaignAccess(restarted)
    result = await access.execute(cid, approval, principal_id="gm")
    final = restarted._load(await restarted.store.read(cid))
    new = build(restarted.rules_context, final, "a")
    assert new.statistics is not None and getattr(new.statistics, attribute) == 9
    assert new.spent == old.spent - (10 if attribute == "ht" else 20)
    assert final.advancement == initial.advancement  # No spendable refund.
    assert len(final.approvals) == len(initial.approvals) + 1
    if attribute == "iq":
        assert new.statistics.will == new.statistics.per == 9
    else:
        assert next(p for p in final.resources.pools if p.id == "fp:a").maximum == 9
    assert result["fright"] == ()
    assert await access.execute(cid, approval, principal_id="gm") == result
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)
    with pytest.raises(ConflictError):
        await access.execute(cid, approval | {"reason": "changed"}, principal_id="gm")


async def test_self_control_worsens_one_step_and_rejects_unrelated_purchases(
    tmp_path: Path,
) -> None:
    cid, play = await prepare_traits(
        tmp_path, Purchase(definition_id="trait:bad-temper", trait=TraitOptions(self_control=15))
    )
    item = await install(
        cid,
        play,
        FrightEffect(table_total=25, trait_choice="worsen-self-control", trait_points=-10),
    )
    state = play._load(await play.store.read(cid))
    draft = state.actors[0].proposal.draft
    purchases = tuple(
        p.model_copy(update={"trait": TraitOptions(self_control=12)})
        if p.definition_id == "trait:bad-temper"
        else p
        for p in draft.purchases
    )
    command = proposal(play, state, item, purchases) | {"related_trait_id": "trait:bad-temper"}
    access = CampaignAccess(play)
    changed = tuple(
        p.model_copy(update={"amount": 11}) if p.definition_id == "attribute:st" else p
        for p in purchases
    )
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Only consequence traits"):
        await access.execute(
            cid,
            command
            | {"draft": draft.model_copy(update={"purchases": changed}).model_dump(mode="json")},
            principal_id="alice",
        )
    assert saved == await play.store.read(cid)
    result = await access.execute(cid, command, principal_id="alice")
    assert isinstance(result["fright"], tuple)
    await access.execute(
        cid,
        {
            "id": "approve",
            "kind": "approve_fright_build",
            "actor_id": "a",
            "expected_revision": result["revision"],
            "fright_id": public_id(item),
            "proposal_id": result["fright"][0]["proposal_id"],
            "reason": "Related disadvantage confirmed",
        },
        principal_id="gm",
    )
    final = play._load(await play.store.read(cid))
    trait = next(
        p for p in final.actors[0].proposal.draft.purchases if p.definition_id == "trait:bad-temper"
    ).trait
    assert trait is not None and trait.self_control == 12
    assert (
        effects(final.resources)[0].adjudicated_build_revision
        == build(play.rules_context, final, "a").revision
    )


@pytest.mark.parametrize(
    "points,choice", [(-1, "quirk"), (-10, "mental"), (-15, "delusion"), (-20, "physical")]
)
async def test_new_trait_requires_exact_cost_then_approved_catalog_purchase(
    tmp_path: Path, points: int, choice: str
) -> None:
    from test_mundane_traits import combined_package

    from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition

    definition = RuleDefinition(
        id="trait:consequence",
        kind=DefinitionKind.TRAIT,
        name="Consequence fixture",
        source_id=combined_package().sources[0].id,
        point_cost=points,
        status=ImplementationStatus.IMPLEMENTED,
    )
    cid, play = await prepare_traits(tmp_path, extra_definitions=(definition,))
    effect = FrightEffect.model_validate(
        {"table_total": 13 if points == -1 else 23, "trait_choice": choice, "trait_points": points}
    )
    item = await install(cid, play, effect)
    state = play._load(await play.store.read(cid))
    purchases = state.actors[0].proposal.draft.purchases + (Purchase(definition_id=definition.id),)
    access = CampaignAccess(play)
    result = await access.execute(cid, proposal(play, state, item, purchases), principal_id="alice")
    assert isinstance(result["fright"], tuple)
    await access.execute(
        cid,
        {
            "id": "approve",
            "kind": "approve_fright_build",
            "actor_id": "a",
            "expected_revision": result["revision"],
            "fright_id": public_id(item),
            "proposal_id": result["fright"][0]["proposal_id"],
            "reason": "GM confirms the consequence classification and its relation to the event",
        },
        principal_id="gm",
    )
    final = play._load(await play.store.read(cid))
    assert any(
        p.definition_id == definition.id and p.cost == points
        for p in build(play.rules_context, final, "a").purchases
    )
    assert final.advancement == state.advancement
