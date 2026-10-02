"""Keep private combat checks out of the actor's numeric tactical summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.attack_visibility import (
    conceal_attack_result,
    pending_secret_source,
    private_attack_result,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.orchestration.membership import member_for, require_control
from wayfarer.orchestration.pipeline import Trusted

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def hide_secret_totals(state: PlayState, encounter_id: str, command_id: str) -> PlayState:
    # Full combat checks remain in GM-only canonical state/events. Public injury,
    # posture and movement remain actual consequences; only this new summary's
    # hidden dice and target numbers are removed.
    return state.model_copy(
        update={
            "encounters": tuple(
                encounter.model_copy(
                    update={
                        "tactical_traces": tuple(
                            trace.model_copy(update={"totals": (), "targets": ()})
                            if trace.command_id == command_id
                            else trace
                            for trace in encounter.tactical_traces
                        )
                    }
                )
                if encounter.id == encounter_id
                else encounter
                for encounter in state.encounters
            )
        }
    )


def preserve_attack_visibility(
    before: PlayState, after: PlayState, encounter_id: str, command_id: str
) -> PlayState:
    encounter = next((e for e in before.encounters if e.id == encounter_id), None)
    source = pending_secret_source(before.resources, encounter) if encounter is not None else None
    if source is None:
        return after
    after = after.model_copy(
        update={"resources": conceal_attack_result(after.resources, source, command_id)}
    )
    return hide_secret_totals(after, encounter_id, command_id)


def may_view_attack(
    play: PlayService, state: PlayState, command_id: str, actor_id: str, principal_id: str
) -> bool:
    member = member_for(state, principal_id)
    if member.role == "gm":
        Trusted(play.engine.reviewer.gm_ids)(principal_id)
        return True
    require_control(member, actor_id, state)
    return not private_attack_result(state.resources, command_id)


def visible_combat_result(
    play: PlayService,
    state: PlayState,
    result: CombatResult,
    command_id: str,
    actor_id: str,
    principal_id: str,
) -> CombatResult:
    if not private_attack_result(state.resources, command_id) or may_view_attack(
        play, state, command_id, actor_id, principal_id
    ):
        return result
    return result.model_copy(update={"injury": None, "unarmed": None})
