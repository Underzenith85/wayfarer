"""Independent expected results for authoritative construction, Characters B10-B31."""

import json
from pathlib import Path

import pytest
from test_mundane_traits import runtime_compiler

from wayfarer.engine.character.compiler import CharacterDraft, Purchase
from wayfarer.engine.character.construction import (
    CharacterConstructionCompiler,
    ConstructionContext,
    ConstructionDraft,
    EquipmentGrant,
    EquipmentSelection,
    Identity,
    LanguageUse,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mental import (
    Relationship,
    RelationshipKind,
    frequency_roll,
    relationship_cost,
)
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.identities import (
    identity_events,
    visible_identity_events,
)

FIXTURE = Path(__file__).parent / "fixtures/gurps/character-construction.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


def exact_draft(**changes: object) -> ConstructionDraft:
    # DX 17 [140] + Fit [5] + Ally (100%, 9 or less) [5] = the 150-point budget.
    character = CharacterDraft(
        name="Mira",
        purchases=(
            Purchase(definition_id="attribute:st", amount=10),
            Purchase(definition_id="attribute:dx", amount=17),
            Purchase(definition_id="attribute:iq", amount=10),
            Purchase(definition_id="attribute:ht", amount=10),
            Purchase(definition_id="trait:fit"),
            Purchase(definition_id="trait:ally-associate"),
        ),
    )
    values: dict[str, object] = {
        "character": character,
        "personal_technology_level": 8,
        "native_language": "trade",
        "native_culture": "home",
        "relationships": (
            Relationship(
                id="ally:associate",
                person_id="associate",
                kind="ally",
                frequency=9,
                character_points_percent=100,
            ),
        ),
        "identities": (
            Identity(id="legal", name="Mira Voss", kind="legal"),
            Identity(id="night", name="Night Heron", kind="secret"),
        ),
        "equipment": (EquipmentSelection(definition_id="equipment:sword"),),
    }
    values.update(changes)
    return ConstructionDraft.model_validate(values)


def construction() -> CharacterConstructionCompiler:
    return CharacterConstructionCompiler(
        runtime_compiler(),
        ConstructionContext(
            campaign_technology_level=8,
            average_starting_wealth=20_000,
            organization_memberships=("watch",),
            equipment_prices={"equipment:sword": 500},
            equipment_grants=(EquipmentGrant(id="watch-kit", definition_id="equipment:armor"),),
        ),
    )


def test_exact_budget_build_settles_wealth_and_keeps_provenance() -> None:
    result = construction().compile(exact_draft())
    assert result.legal and result.spent == 150 and result.remaining == 0
    assert result.character is not None
    assert result.character.wealth.opening_balance == 20_000
    assert result.character.wealth.remaining == 19_500
    assert [
        (entry.kind, entry.amount, entry.provenance_id)
        for entry in result.character.wealth.transactions
    ] == [
        ("starting-wealth", 20_000, None),
        ("grant", 0, "watch-kit"),
        ("purchase", -500, None),
    ]
    assert result.character.relationships[0].person_id == "associate"


def test_budget_tl_language_and_equipment_failures_are_typed() -> None:
    under = construction().compile(exact_draft(relationships=()))
    assert "relationship.missing_facts" in {diagnostic.code for diagnostic in under.diagnostics}
    mismatch = construction().compile(
        exact_draft(
            personal_technology_level=7,
            language_uses=(LanguageUse(language_id="unknown", mode="spoken"),),
            equipment=(EquipmentSelection(definition_id="equipment:unknown"),),
        )
    )
    assert {
        "background.personal_tl_unavailable",
        "background.language_inadequate",
        "wealth.equipment_unavailable",
    } <= {diagnostic.code for diagnostic in mismatch.diagnostics}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_relationship_prices_are_source_derived(case: dict[str, object]) -> None:
    assert (
        relationship_cost(Relationship.model_validate(case["relationship"])) == case["point_cost"]
    )


def test_relationship_availability_uses_recorded_dice() -> None:
    ally = exact_draft().relationships[0]
    assert frequency_roll(ally, RecordedDice([3, 3, 3])).appears
    assert not frequency_roll(ally, RecordedDice([4, 4, 4])).appears


@pytest.mark.parametrize(
    ("kind", "facts"),
    (
        ("ally", {"character_points_percent": 100}),
        ("contact", {"contact_skill": 12}),
        ("patron", {"patron_power": "wealthy"}),
        ("dependent", {"character_points_percent": 50}),
        ("enemy", {}),
    ),
)
def test_relationships_require_exact_purchased_traits_and_authored_npc_facts(
    kind: RelationshipKind, facts: dict[str, object]
) -> None:
    base = exact_draft()
    purchases = tuple(
        purchase.model_copy(update={"amount": 16})
        if purchase.definition_id == "attribute:dx"
        else purchase
        for purchase in base.character.purchases
        if purchase.definition_id != "trait:ally-associate"
    ) + (Purchase(definition_id=f"trait:{kind}-associate"),)
    relationship = Relationship(
        id=f"{kind}:associate",
        person_id="associate",
        kind=kind,
        frequency=9,
        **facts,
    )
    draft = base.model_copy(
        update={
            "character": base.character.model_copy(update={"purchases": purchases}),
            "relationships": (relationship,),
        }
    )
    configured = construction()
    relaxed = CharacterConstructionCompiler(
        configured.compiler,
        configured.context.model_copy(update={"require_exact_point_budget": False}),
    )
    result = relaxed.compile(draft)
    assert result.legal and result.character is not None
    assert result.character.relationships == (relationship,)
    assert f"trait:{kind}-associate" in {
        purchase.definition_id for purchase in result.character.build.trait_purchases
    }

    missing = relaxed.compile(draft.model_copy(update={"relationships": ()}))
    assert "relationship.missing_facts" in {diagnostic.code for diagnostic in missing.diagnostics}
    unpurchased = relaxed.compile(
        draft.model_copy(
            update={"character": draft.character.model_copy(update={"purchases": purchases[:-1]})}
        )
    )
    assert "relationship.unpurchased" in {diagnostic.code for diagnostic in unpurchased.diagnostics}


def test_relationship_construction_rejects_unpurchased_frequency_variants() -> None:
    draft = exact_draft()
    varied = draft.relationships[0].model_copy(update={"frequency": 12})
    result = construction().compile(draft.model_copy(update={"relationships": (varied,)}))
    assert "relationship.binding_mismatch" in {diagnostic.code for diagnostic in result.diagnostics}


def test_secret_identity_never_enters_an_unrelated_public_projection() -> None:
    events = identity_events("mira", exact_draft().identities)
    owner = CampaignMember(principal_id="mira-player", role="player", actor_ids=("mira",))
    observer = CampaignMember(principal_id="observer", role="player")
    assert {event.identity_id for event in visible_identity_events(events, owner)} == {
        "legal",
        "night",
    }
    assert [event.identity_id for event in visible_identity_events(events, observer)] == ["legal"]
