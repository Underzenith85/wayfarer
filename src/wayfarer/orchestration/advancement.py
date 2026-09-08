"""Transactional advancement ledger and explicitly approved rules migrations."""

from __future__ import annotations

import json

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.character.compiler import CharacterDraft, ValidatedBuild, pool_limits
from wayfarer.character.power import CharacterProposal
from wayfarer.character.statistics import RuntimePool, carry_over
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.catalog import reference
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.adjudication import expire_rulings
from wayfarer.simulation.advancement import (
    AdvancementEntry,
    AdvancementPreview,
    BuildDiff,
    MigrationEntry,
    MigrationPreview,
)
from wayfarer.simulation.encounter_context import EncounterSceneBinding, bind_scene, migrate_unique
from wayfarer.simulation.resources import Id, Pool, Record
from wayfarer.simulation.scenes import ActorScene


class GrantPoints(Record):
    id: Id
    actor_id: Id
    target_actor_id: Id
    expected_revision: int = Field(ge=0)
    points: int = Field(ge=1, le=10000)
    reason: str = Field(min_length=1, max_length=2000)


class AdvanceCharacter(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    expected_build_revision: str
    draft: CharacterDraft
    reason: str = Field(min_length=1, max_length=2000)


class ApplyMigration(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    expected_from_digest: str
    reason: str = Field(min_length=1, max_length=2000)
    actor_scenes: tuple[ActorScene, ...] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    encounter_scenes: tuple[EncounterSceneBinding, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )


def _build(play: PlayService, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next((candidate for candidate in state.actors if candidate.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Unknown character")
    build = play.engine.reviewer.review(actor.proposal).compilation.build
    if build is None:
        raise ValidationError("Canonical character no longer compiles")
    return build


def _diff(actor_id: str, before: ValidatedBuild, after: ValidatedBuild) -> BuildDiff:
    old = {entry.definition_id: entry for entry in before.purchases}
    new = {entry.definition_id: entry for entry in after.purchases}
    old_values = {value.target: str(value.value) for value in before.sheet.values}
    new_values = {value.target: str(value.value) for value in after.sheet.values}
    changed = tuple(
        (key, old_values.get(key, ""), new_values.get(key, ""))
        for key in sorted(old_values.keys() | new_values.keys())
        if old_values.get(key) != new_values.get(key)
    )
    return BuildDiff(
        actor_id=actor_id,
        old_revision=before.revision,
        new_revision=after.revision,
        old_spent=before.spent,
        new_spent=after.spent,
        added=tuple(sorted(new.keys() - old.keys())),
        removed=tuple(sorted(old.keys() - new.keys())),
        changed_values=changed,
    )


def _refreshed(pool: Pool, maximum: int, build: ValidatedBuild) -> Pool:
    """Move a pool ceiling for a recompiled build without healing anything.

    The prototype package keeps its original clamp. Profile builds preserve the
    deficit, so purchased HP or FP raise the current value by the same amount.
    """

    if build.statistics is None:
        return pool.model_copy(update={"maximum": maximum, "current": min(pool.current, maximum)})
    if pool.fatigue is not None and maximum - (pool.maximum - pool.current) < -maximum:
        raise ValidationError("FP reduction requires resolving the outstanding fatigue deficit")
    if pool.injury is not None or pool.fatigue is not None:
        return pool.model_copy(
            update={"maximum": maximum, "current": maximum - (pool.maximum - pool.current)}
        )
    carried = carry_over(RuntimePool(pool.current, pool.maximum), maximum)
    return pool.model_copy(update={"maximum": carried.maximum, "current": carried.current})


def _balance(state: PlayState, actor_id: str) -> int:
    return sum(entry.points for entry in state.advancement if entry.actor_id == actor_id)


class AdvancementService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def preview(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> AdvancementPreview:
        command = self._advance(value, authenticated_actor_id)
        state = self.play._load(await self.play.store.read(cid))
        before = _build(self.play, state, command.actor_id)
        if (
            command.expected_revision != state.revision
            or command.expected_build_revision != before.revision
        ):
            raise ConflictError("Advancement preview context changed")
        review = self.play.engine.reviewer.review(CharacterProposal(draft=command.draft))
        after = review.compilation.build
        if after is None or review.status in ("illegal", "blocked"):
            raise ValidationError("Advancement is not legal under campaign rules")
        delta = after.spent - before.spent
        if delta < 0:
            raise ValidationError("Refunds require GM authorization")
        available = _balance(state, command.actor_id)
        if delta > available:
            raise ValidationError("Advancement overspends earned points")
        return AdvancementPreview(
            actor_id=command.actor_id,
            draft=command.draft,
            points_available=available,
            points_delta=delta,
            diff=_diff(command.actor_id, before, after),
        )

    async def grant(self, cid: str, value: object, *, authenticated_gm_id: str) -> AdvancementEntry:
        try:
            command = GrantPoints.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid point grant") from exc
        if (
            command.actor_id != authenticated_gm_id
            or authenticated_gm_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("Point grants require GM authority")
        payload = self._payload("grant", command.model_dump(mode="json"))

        def resolve(campaign: Campaign) -> Event:
            state = self.play._load(campaign)
            build = _build(self.play, state, command.target_actor_id)
            entry = AdvancementEntry(
                id=command.id,
                actor_id=command.target_actor_id,
                kind="earned",
                points=command.points,
                revision=state.revision + 1,
                build_before=build.revision,
                build_after=build.revision,
                reason=command.reason,
            )
            updated = self._revision(state, advancement=state.advancement + (entry,))
            updated = self.play.checkpoint(updated)
            self.play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            return Event(
                input=payload, action="advancement", outcome=entry.model_dump_json(), roll=None
            )

        committed = await self.play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        return PlayState.model_validate_json(committed["state"]["play_json"]).advancement[-1]

    def reduce_purchase(
        self, state: PlayState, command: AdvanceCharacter, *, revision: int
    ) -> PlayState:
        """Pure purchase reducer, shared by ordinary advancement and queued downtime."""
        if command.expected_revision != state.revision or revision not in (
            state.revision,
            state.revision + 1,
        ):
            raise ConflictError("Advancement revision changed")
        before = _build(self.play, state, command.actor_id)
        if before.revision != command.expected_build_revision:
            raise ConflictError("Character build changed")
        current = _balance(state, command.actor_id)
        review = self.play.engine.reviewer.review(CharacterProposal(draft=command.draft))
        after = review.compilation.build
        if after is None or review.status != "automatic":
            raise ValidationError("Advancement requires a legal automatically approved build")
        cost = after.spent - before.spent
        if cost < 0 or cost > current:
            raise ValidationError("Advancement point balance is invalid")
        approval = self.play.engine.reviewer.approve(
            CharacterProposal(draft=command.draft),
            campaign_id=state.campaign_id,
            actor_id=command.actor_id,
            revision=revision,
        )
        entry = AdvancementEntry(
            id=command.id,
            actor_id=command.actor_id,
            kind="purchase",
            points=-cost,
            revision=revision,
            build_before=before.revision,
            build_after=after.revision,
            reason=command.reason,
        )
        actors = tuple(
            actor.model_copy(
                update={
                    "proposal": CharacterProposal(draft=command.draft),
                    "approval": approval,
                }
            )
            if actor.actor_id == command.actor_id
            else actor
            for actor in state.actors
        )
        owners = tuple(
            owner.model_copy(
                update={"definitions": tuple(p.definition_id for p in after.purchases)}
            )
            if owner.actor_id == command.actor_id
            else owner
            for owner in state.resources.owners
        )
        maxima = pool_limits(after)
        pools = tuple(
            _refreshed(pool, maxima[pool.id.split(":", 1)[0]], after)
            if pool.id in (f"hp:{command.actor_id}", f"fp:{command.actor_id}")
            else pool
            for pool in state.resources.pools
        )
        return state.model_copy(
            update={
                "revision": revision,
                "actors": actors,
                "approvals": state.approvals + (approval,),
                "advancement": state.advancement + (entry,),
                "resources": state.resources.model_copy(
                    update={"owners": owners, "pools": pools, "revision": revision}
                ),
                "rulings": expire_rulings(state.rulings, revision, state.resources.game_time),
            }
        )

    async def advance(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> AdvancementEntry:
        command = self._advance(value, authenticated_actor_id)
        # Validate inside the transaction so a committed retry reaches its receipt first.
        payload = self._payload("advance", command.model_dump(mode="json"))

        def resolve(campaign: Campaign) -> Event:
            state = self.play._load(campaign)
            updated = self.reduce_purchase(state, command, revision=state.revision + 1)
            entry = updated.advancement[-1]
            updated = self.play.checkpoint(updated)
            self.play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            return Event(
                input=payload, action="advancement", outcome=entry.model_dump_json(), roll=None
            )

        committed = await self.play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        result = PlayState.model_validate_json(committed["state"]["play_json"]).advancement[-1]
        if result.id != command.id:
            raise ConflictError("Command ID belongs to another ledger entry")
        return result

    @staticmethod
    def _advance(value: object, identity: str) -> AdvanceCharacter:
        try:
            command = AdvanceCharacter.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid advancement") from exc
        if command.actor_id != identity:
            raise ValidationError("Advancement actor is not authorized")
        return command

    @staticmethod
    def _payload(operation: str, command: object) -> str:
        return json.dumps(
            {"operation": operation, "command": command}, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _revision(state: PlayState, **changes: object) -> PlayState:
        revision = state.revision + 1
        resources = changes.pop("resources", state.resources)
        assert hasattr(resources, "model_copy")
        changes.update(
            revision=revision,
            resources=resources.model_copy(update={"revision": revision}),
            rulings=expire_rulings(state.rulings, revision, state.resources.game_time),
        )
        return state.model_copy(update=changes)


class MigrationService:
    def __init__(
        self,
        current: PlayService,
        target: PlayService,
        *,
        authority: frozenset[str] | None = None,
        from_profile: str | None = None,
        to_profile: str | None = None,
    ) -> None:
        if current.store is not target.store:
            raise ValidationError("Migration services must share a store")
        self.current, self.target = current, target
        # Rules migration authority defaults to configured GM identities. A caller
        # that owns another explicit boundary (the setup host) names it here.
        self.authority = current.engine.reviewer.gm_ids if authority is None else authority
        self.from_profile, self.to_profile = from_profile, to_profile

    async def preview(self, cid: str) -> MigrationPreview:
        state = self.current._load(await self.current.store.read(cid))
        diffs: list[BuildDiff] = []
        for actor in state.actors:
            before = _build(self.current, state, actor.actor_id)
            review = self.target.engine.reviewer.review(actor.proposal)
            after = review.compilation.build
            if after is None or review.status in ("illegal", "blocked"):
                raise ValidationError("Target rules invalidate a character")
            diffs.append(_diff(actor.actor_id, before, after))
        return MigrationPreview(
            from_digest=self.current.engine.digest,
            to_digest=self.target.engine.digest,
            actor_diffs=tuple(diffs),
        )

    async def apply(
        self, cid: str, value: object, *, authenticated_gm_id: str, payload: str | None = None
    ) -> MigrationEntry:
        try:
            command = ApplyMigration.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid migration approval") from exc
        if command.actor_id != authenticated_gm_id or authenticated_gm_id not in self.authority:
            raise ValidationError("Rules migration requires GM authority")
        if command.expected_from_digest != self.current.engine.digest:
            raise ConflictError("Migration source configuration changed")
        if payload is None:
            payload = AdvancementService._payload(
                "rules-migration", command.model_dump(mode="json")
            )
        duplicate = await self.current.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            state = PlayState.model_validate_json(duplicate["play_json"])
            entry = next((value for value in state.migrations if value.id == command.id), None)
            if entry is None:
                raise ConflictError("Command ID belongs to another operation")
            return entry
        preview = await self.preview(cid)

        # A configured GM records GM approvals; any other authorized approver (the
        # setup host) can only carry characters that stay within automatic limits.
        gm = authenticated_gm_id in self.target.engine.reviewer.gm_ids

        def resolve(campaign: Campaign) -> Event:
            state = self.current._load(campaign)
            approvals = []
            actors = []
            for actor in state.actors:
                approval = self.target.engine.reviewer.approve(
                    actor.proposal,
                    campaign_id=cid,
                    actor_id=actor.actor_id,
                    revision=state.revision + 1,
                    approver_id=authenticated_gm_id if gm else None,
                    reason=command.reason if gm else "",
                )
                approvals.append(approval)
                actors.append(actor.model_copy(update={"approval": approval}))
            entry = MigrationEntry(
                id=command.id,
                actor_id=command.actor_id,
                revision=state.revision + 1,
                from_digest=preview.from_digest,
                to_digest=preview.to_digest,
                reason=command.reason,
                from_profile=self.from_profile,
                to_profile=self.to_profile,
            )
            updated = AdvancementService._revision(
                state,
                configuration_digest=self.target.engine.digest,
                actors=tuple(actors),
                approvals=state.approvals + tuple(approvals),
                migrations=state.migrations + (entry,),
            )
            if command.actor_scenes is not None:
                if state.actor_scenes:
                    raise ValidationError("Scene adoption cannot relocate existing scene cursors")
                target_scenes = self.target.engine.rules.scenes
                if target_scenes is None:
                    raise ValidationError("Scene adoption requires target scene rules")
                scene_map = {s.id: s for s in target_scenes.scenes}
                entities = {e.id: e for e in state.world.entities}
                if any(
                    c.scene_id not in scene_map
                    or c.actor_id not in entities
                    or entities[c.actor_id].location_id != scene_map[c.scene_id].location_id
                    for c in command.actor_scenes
                ):
                    raise ValidationError(
                        "Explicit actor scene mapping must preserve world locations"
                    )
                updated = updated.model_copy(update={"actor_scenes": command.actor_scenes})
            elif not state.actor_scenes and self.target.engine.rules.scenes is not None:
                raise ValidationError("Scene-less migration requires explicit actor scene mapping")
            if command.encounter_scenes:
                combat = self.target.engine.rules.combat
                bindings = {b.encounter_id: b.scene_id for b in command.encounter_scenes}
                if (
                    combat is None
                    or len(bindings) != len(command.encounter_scenes)
                    or not set(bindings) <= {e.id for e in updated.encounters}
                ):
                    raise ValidationError("Invalid encounter scene migration bindings")
                updated = updated.model_copy(
                    update={
                        "encounters": tuple(
                            bind_scene(e, self.target.engine.rules.scenes, combat, bindings[e.id])
                            if e.id in bindings
                            else e
                            for e in updated.encounters
                        )
                    }
                )
            updated = migrate_unique(
                updated, self.target.engine.rules.scenes, self.target.engine.rules.combat
            )
            if self.target.engine.rules.scenes is not None and any(
                e.scene_id is None for e in updated.encounters
            ):
                raise ValidationError(
                    "Rules migration requires explicit ambiguous encounter scene mappings"
                )
            if self.target.engine.rules.party is not None:
                from wayfarer.simulation.party import migrate

                updated = migrate(updated)
            campaign["rules_ref"] = reference(self.target.engine.resources.rules)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            self.target.engine.validate(updated)
            return Event(
                input=payload, action="rules-migration", outcome=entry.model_dump_json(), roll=None
            )

        committed = await self.current.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        return PlayState.model_validate_json(committed["state"]["play_json"]).migrations[-1]
