"""B57 independent evidence: 5 points/use, maximum three, once/session/level."""

from dataclasses import replace

import pytest
from test_mundane_traits import runtime_compiler
from test_resources import engine
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import Purchase, ValidatedBuild
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.resources import Item, Owner, ResourceState, Transfer
from wayfarer.engine.simulation.traits.gizmos import (
    BeginGizmoSession,
    GizmoEligibility,
    RevealGizmo,
    begin_session,
    reveal_gizmo,
)
from wayfarer.errors import ConflictError, ValidationError


def build(levels: int = 1) -> tuple[ValidatedBuild, dict[str, RuleDefinition]]:
    compiler = runtime_compiler()
    result = compiler.compile(
        gurps_draft(Purchase(definition_id="trait:advantage:gizmos", amount=levels))
    )
    assert result.build is not None, result.diagnostics
    assert result.spent == levels * 5
    return result.build, dict(compiler.definitions)


def session() -> ResourceState:
    state = ResourceState(
        owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100))
    )
    return begin_session(
        state,
        BeginGizmoSession(id="open", actor_id="gm", expected_revision=0, session_id="one"),
        authorized_actor_id="gm",
        system=True,
    )[0]


def approval(identifier: str = "one") -> GizmoEligibility:
    return GizmoEligibility(
        id=identifier,
        actor_id="a",
        session_id="one",
        item=Item(id="gizmo-" + identifier, definition_id="arrow", owner_id="a"),
        category="common-device",
        pocket_sized=True,
        could_have_carried=True,
        inexpensive=True,
        widely_available_at_actor_tl=True,
    )


def reveal(state: ResourceState, approved: GizmoEligibility, levels: int = 1) -> ResourceState:
    character, definitions = build(levels)
    return reveal_gizmo(
        engine(),
        state,
        RevealGizmo(
            id="reveal-" + approved.id,
            actor_id="a",
            expected_revision=state.revision,
            session_id=approved.session_id,
            eligibility_id=approved.id,
        ),
        character,
        definitions,
        (approved,),
        authorized_actor_id="a",
        system=True,
    )[0]


def test_price_and_source_maximum_are_enforced_by_compiler() -> None:
    for levels in (1, 2, 3):
        build(levels)
    result = runtime_compiler().compile(
        gurps_draft(Purchase(definition_id="trait:advantage:gizmos", amount=4))
    )
    assert result.build is None


@pytest.mark.parametrize("category", ["owned", "character-concept", "common-device"])
def test_each_source_category_enters_actual_inventory(category: str) -> None:
    approved = approval()
    if category == "owned":
        approved = approved.model_copy(update={"category": "owned", "owned_but_undeclared": True})
    elif category == "character-concept":
        approved = approved.model_copy(
            update={
                "category": "character-concept",
                "probably_owned": True,
                "matches_character_concept": True,
                "minor_or_ignorable": True,
            }
        )
    state = session()
    assert state.items == ()  # Unrevealed equipment is outside search/damage/custody state.
    updated = reveal(state, approved)
    assert updated.items == (approved.item,)
    assert updated.revision == state.revision + 1
    assert engine().carried_weight(updated, "a") == 1
    with pytest.raises(ValidationError, match="exhausted"):
        reveal(updated, approval("second"))
    # Once revealed, the same real instance follows ordinary inventory custody.
    transferred = engine().apply(
        updated,
        Transfer(
            id="give",
            actor_id="a",
            expected_revision=updated.revision,
            item_id=approved.item.id,
            quantity=1,
            owner_id="b",
        ),
    )
    assert next(i for i in transferred.items if i.id == approved.item.id).owner_id == "b"


@pytest.mark.parametrize(
    "changes",
    [
        {"pocket_sized": False},
        {"could_have_carried": False},
        {"inexpensive": False},
        {"widely_available_at_actor_tl": False},
        {"gadgeteer_invention": True},
        {"category": "owned"},
        {"category": "character-concept", "probably_owned": True},
        {"actor_id": "b"},
        {"item": Item(id="wrong", definition_id="arrow", owner_id="b")},
        {"item": Item(id="stack", definition_id="arrow", owner_id="a", quantity=20)},
    ],
)
def test_invalid_eligibility_never_creates_or_consumes(changes: dict[str, object]) -> None:
    state = session()
    with pytest.raises(ValidationError):
        reveal(state, approval().model_copy(update=changes))
    assert state.items == ()
    assert reveal(state, approval()).items == (approval().item,)


def test_three_levels_allow_exactly_three_uses_without_game_time_reset() -> None:
    state = session()
    for identifier in ("1", "2", "3"):
        state = reveal(state, approval(identifier), 3)
    assert len(state.items) == 3
    later = state.model_copy(update={"game_time": 100000})
    with pytest.raises(ValidationError, match="exhausted"):
        reveal(later, approval("4"), 3)


def test_reset_requires_gm_new_session_and_keeps_existing_items() -> None:
    state = reveal(session(), approval())
    command = BeginGizmoSession(
        id="reset", actor_id="gm", expected_revision=state.revision, session_id="two"
    )
    with pytest.raises(ValidationError, match="GM authority"):
        begin_session(state, command, authorized_actor_id="gm")
    updated, outcome = begin_session(state, command, authorized_actor_id="gm", system=True)
    assert updated.items == state.items
    assert begin_session(updated, command, authorized_actor_id="gm", system=True) == (
        updated,
        outcome,
    )
    approved = approval("new").model_copy(update={"session_id": "two"})
    updated = reveal(updated, approved)
    assert len(updated.items) == 2
    # An exact retry from an earlier session returns its receipt, never resets.
    old_open = BeginGizmoSession(id="open", actor_id="gm", expected_revision=0, session_id="one")
    replayed, _ = begin_session(updated, old_open, authorized_actor_id="gm", system=True)
    assert replayed == updated
    with pytest.raises(ValidationError, match="exhausted"):
        reveal(replayed, approval("another").model_copy(update={"session_id": "two"}))
    with pytest.raises(ValidationError, match="current"):
        reveal(updated, approval("old"))
    with pytest.raises(ConflictError, match="already been opened"):
        begin_session(
            updated,
            command.model_copy(
                update={"id": "reopen", "session_id": "one", "expected_revision": updated.revision}
            ),
            authorized_actor_id="gm",
            system=True,
        )


def test_exact_retry_returns_receipt_and_payload_reuse_and_stale_revision_reject() -> None:
    state = session()
    character, definitions = build(2)
    approved = approval()
    command = RevealGizmo(
        id="use",
        actor_id="a",
        expected_revision=state.revision,
        session_id="one",
        eligibility_id=approved.id,
    )

    def apply(
        value: ResourceState, request: RevealGizmo, system: bool = True
    ) -> tuple[ResourceState, object]:
        return reveal_gizmo(
            engine(),
            value,
            request,
            character,
            definitions,
            (approved,),
            authorized_actor_id="a",
            system=system,
        )

    updated, outcome = apply(state, command)
    assert apply(updated, command) == (updated, outcome)
    assert len(updated.items) == 1
    with pytest.raises(ValidationError, match="authority"):
        apply(updated, command, False)
    with pytest.raises(ConflictError, match="already used"):
        apply(updated, command.model_copy(update={"eligibility_id": "other"}))
    with pytest.raises(ConflictError, match="revision"):
        apply(updated, command.model_copy(update={"id": "stale"}))
    with pytest.raises(ConflictError, match="already entered"):
        reveal(updated, approved, 2)


def test_no_purchase_and_unknown_spec_and_capacity_reject_atomically() -> None:
    state = session()
    character, definitions = build()
    no_trait = replace(character, trait_purchases=())
    request = RevealGizmo(
        id="use",
        actor_id="a",
        expected_revision=state.revision,
        session_id="one",
        eligibility_id="one",
    )
    with pytest.raises(ValidationError, match="purchased"):
        reveal_gizmo(
            engine(),
            state,
            request,
            no_trait,
            definitions,
            (approval(),),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError, match="Unknown equipment"):
        reveal(
            state,
            approval().model_copy(
                update={"item": Item(id="unknown", definition_id="unapproved", owner_id="a")}
            ),
        )
    overloaded = state.model_copy(update={"owners": (Owner(actor_id="a", capacity=0),)})
    with pytest.raises(ValidationError, match="capacity"):
        reveal(overloaded, approval())
    assert reveal(state, approval()).items == (approval().item,)
