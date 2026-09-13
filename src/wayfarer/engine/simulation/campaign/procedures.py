"""Family-local dispatch for campaign administration and law commands."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.campaign.adjudication import expire_rulings
from wayfarer.engine.simulation.campaign.administration import (
    AdministrationOutcome,
    AdministrationRules,
    CampaignAdministrationCommand,
    apply_administration,
)
from wayfarer.engine.simulation.campaign.economics import (
    EconomicCommand,
    EconomicsOutcome,
    EconomicsRules,
    apply_economics,
)
from wayfarer.engine.simulation.campaign.law import (
    LawCommand,
    LawOutcome,
    LawRules,
    apply_law,
)
from wayfarer.engine.simulation.resources import Advance, ResourceState, Transfer
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.resource_engine import ResourceEngine

CampaignOutcome = AdministrationOutcome | LawOutcome | EconomicsOutcome
CampaignCommand = CampaignAdministrationCommand | LawCommand | EconomicCommand


class CampaignProcedureEngine:
    """Resolve campaign commands without adding branches to the action reducer."""

    def __init__(
        self,
        resources: ResourceEngine,
        reviewer: PowerReviewer,
        administration: AdministrationRules | None,
        law: LawRules | None,
        economics: EconomicsRules | None,
    ) -> None:
        self.resources = resources
        self.reviewer = reviewer
        self.administration = administration
        self.law = law
        self.economics = economics

    def _advance(
        self, actor_id: str, rng: RandomSource
    ) -> Callable[[ResourceState, int, str], ResourceState]:
        def advance(state: ResourceState, to: int, parent_id: str) -> ResourceState:
            identity = "campaign-clock:" + hashlib.sha256(parent_id.encode()).hexdigest()
            return self.resources.apply(
                state,
                Advance(
                    id=identity,
                    actor_id=actor_id,
                    expected_revision=state.revision,
                    to=to,
                ),
                system=True,
                rng=rng,
            )

        return advance

    def apply(
        self,
        state: PlayState,
        command: CampaignCommand,
        *,
        rng: RandomSource,
        system: bool = False,
    ) -> tuple[PlayState, CampaignOutcome]:
        if command.kind in {"reaction", "knowledge", "award", "time-use", "trap"}:
            if self.administration is None:
                raise ValidationError("Campaign administration command is not enabled")
            actor = next((item for item in state.actors if item.actor_id == command.actor_id), None)
            compiled = (
                self.reviewer.compiler.compile(actor.proposal.draft).build
                if actor is not None
                else None
            )
            build_revision = compiled.revision if compiled is not None else "unchanged"
            admin, resources, world, advancement, outcome = apply_administration(
                state.administration,
                state.resources,
                state.world,
                state.advancement,
                command,
                self.administration,
                rng=rng,
                build_revision=build_revision,
                advance=self._advance(command.actor_id, rng),
                system=system,
            )
            if resources is state.resources:
                return state, outcome
            updated = state.model_copy(
                update={
                    "revision": resources.revision,
                    "resources": resources,
                    "world": world,
                    "advancement": advancement,
                    "administration": admin,
                    "rulings": expire_rulings(
                        state.rulings, resources.revision, resources.game_time
                    ),
                }
            )
            return updated, outcome
        if command.kind in {
            "trade",
            "exchange",
            "job-search",
            "job-settlement",
            "cost-of-living",
            "hire",
            "hireling-pay",
            "loyalty",
        }:
            if self.economics is None:
                raise ValidationError("Economics command is not enabled")
            actor = next((item for item in state.actors if item.actor_id == command.actor_id), None)
            if actor is None:
                raise ValidationError("Economics actor is not a playable campaign actor")
            compiled = self.reviewer.compiler.compile(actor.proposal.draft).build
            if compiled is None:
                raise ValidationError("Economics actor does not have a valid build")
            values = {value.target: value.value for value in compiled.sheet.values}

            def transfer(
                current: ResourceState,
                item_id: str,
                quantity: int,
                owner_id: str,
                new_item_id: str | None,
                parent_id: str,
            ) -> ResourceState:
                item = next((value for value in current.items if value.id == item_id), None)
                if item is None:
                    raise ValidationError("Trade item no longer exists")
                identity = "economics-transfer:" + hashlib.sha256(parent_id.encode()).hexdigest()
                return self.resources.apply(
                    current,
                    Transfer(
                        id=identity,
                        actor_id=item.owner_id,
                        expected_revision=current.revision,
                        item_id=item_id,
                        quantity=quantity,
                        owner_id=owner_id,
                        new_item_id=new_item_id,
                    ),
                    system=True,
                    rng=rng,
                )

            economics, resources, economics_outcome = apply_economics(
                state.economics,
                state.resources,
                state.administration,
                command,
                self.economics,
                world=state.world,
                build_values=values,
                purchased_ids=frozenset(value.definition_id for value in compiled.purchases),
                rng=rng,
                transfer=transfer,
                system=system,
            )
            if resources is state.resources:
                return state, economics_outcome
            return (
                state.model_copy(
                    update={
                        "revision": resources.revision,
                        "resources": resources,
                        "economics": economics,
                        "rulings": expire_rulings(
                            state.rulings, resources.revision, resources.game_time
                        ),
                    }
                ),
                economics_outcome,
            )
        if self.law is None:
            raise ValidationError("Law command is not enabled")
        law_command = cast(LawCommand, command)
        law, resources, law_outcome = apply_law(
            state.law,
            state.resources,
            law_command,
            self.law,
            rng=rng,
            known_fact_ids=frozenset(item.id for item in state.world.facts),
            advance=self._advance(command.actor_id, rng),
            system=system,
        )
        if resources is state.resources:
            return state, law_outcome
        updated = state.model_copy(
            update={
                "revision": resources.revision,
                "resources": resources,
                "law": law,
                "rulings": expire_rulings(state.rulings, resources.revision, resources.game_time),
            }
        )
        return updated, law_outcome
