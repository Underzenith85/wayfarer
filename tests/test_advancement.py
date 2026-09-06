"""Advancement ledger and explicit migration contracts."""

from pathlib import Path

import pytest
from test_actions import actor_setup, campaign, engine, resource_seed, world

from wayfarer.character.compiler import Purchase
from wayfarer.errors import ValidationError
from wayfarer.orchestration.advancement import (
    AdvanceCharacter,
    AdvancementService,
    ApplyMigration,
    GrantPoints,
    MigrationService,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.simulation.actions import ActionEngine


async def setup(tmp_path: Path) -> tuple[str, PlayService, AdvancementService]:
    reducer = engine()
    play = PlayService(AsyncSQLiteStore(tmp_path / "advancement.sqlite", 10), reducer)
    initial = campaign(reducer)
    await play.create(initial, world(), resource_seed(), (actor_setup(),))
    return initial["id"], play, AdvancementService(play)


def upgraded(revision: int, build_revision: str) -> AdvanceCharacter:
    setup = actor_setup()
    purchases = tuple(
        Purchase(definition_id=p.definition_id, amount=8)
        if p.definition_id == "skill:observation"
        else p
        for p in setup.proposal.draft.purchases
    )
    return AdvanceCharacter(
        id="buy-observation",
        actor_id="a",
        expected_revision=revision,
        expected_build_revision=build_revision,
        draft=setup.proposal.draft.model_copy(update={"purchases": purchases}),
        reason="Training",
    )


async def test_grant_preview_purchase_retry_and_restart(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    grant = GrantPoints(
        id="award",
        actor_id="gm",
        target_actor_id="a",
        expected_revision=0,
        points=4,
        reason="Solved the opening scene",
    )
    awarded = await service.grant(cid, grant, authenticated_gm_id="gm")
    assert awarded.kind == "earned" and awarded.points == 4
    command = upgraded(1, build.revision)
    preview = await service.preview(cid, command, authenticated_actor_id="a")
    assert preview.points_available == 4 and preview.points_delta == 4
    results = [await service.advance(cid, command, authenticated_actor_id="a") for _ in range(2)]
    assert results[0] == results[1]
    assert results[0].kind == "purchase" and results[0].points == -4
    restarted = AdvancementService(PlayService(play.store, play.engine))
    state = restarted.play._load(await restarted.play.store.read(cid))
    assert sum(entry.points for entry in state.advancement) == 0
    assert len(state.advancement) == 2
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_overspending_illegal_and_unauthorized_grants_mutate_nothing(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="overspends"):
        await service.preview(cid, upgraded(0, build.revision), authenticated_actor_id="a")
    with pytest.raises(ValidationError, match="GM authority"):
        await service.grant(
            cid,
            GrantPoints(
                id="forged",
                actor_id="a",
                target_actor_id="a",
                expected_revision=0,
                points=100,
                reason="Forged",
            ),
            authenticated_gm_id="a",
        )
    assert await play.store.read(cid) == before


async def test_migration_preview_apply_and_failed_migration_is_atomic(tmp_path: Path) -> None:
    cid, current, _ = await setup(tmp_path)
    base = current.engine
    target_engine = ActionEngine(
        base.reviewer,
        base.resources,
        base.rules.model_copy(update={"version": base.rules.version + 1}),
    )
    target = PlayService(current.store, target_engine)
    migrations = MigrationService(current, target)
    preview = await migrations.preview(cid)
    assert preview.from_digest == base.digest and preview.to_digest == target_engine.digest
    before = await current.store.read(cid)
    with pytest.raises(ValidationError, match="GM authority"):
        await migrations.apply(
            cid,
            ApplyMigration(
                id="bad",
                actor_id="a",
                expected_revision=0,
                expected_from_digest=base.digest,
                reason="No authority",
            ),
            authenticated_gm_id="a",
        )
    assert await current.store.read(cid) == before
    entry = await migrations.apply(
        cid,
        ApplyMigration(
            id="migration",
            actor_id="gm",
            expected_revision=0,
            expected_from_digest=base.digest,
            reason="Approved package upgrade",
        ),
        authenticated_gm_id="gm",
    )
    assert entry.to_digest == target_engine.digest
    assert (
        await migrations.apply(
            cid,
            ApplyMigration(
                id="migration",
                actor_id="gm",
                expected_revision=0,
                expected_from_digest=base.digest,
                reason="Approved package upgrade",
            ),
            authenticated_gm_id="gm",
        )
        == entry
    )
    migrated = target._load(await target.store.read(cid))
    assert migrated.configuration_digest == target_engine.digest
    assert await target.store.read(cid) == await target.store.replay(cid)
