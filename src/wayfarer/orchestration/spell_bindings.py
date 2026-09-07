"""Derive private spell roll context exclusively from approved, pinned builds."""

from typing import Literal

from pydantic import Field

from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.gurps_magic import definitions, magery_level
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.resources import Id, Record
from wayfarer.simulation.spells import PROFILE, SPELLS, SpellCommand, SpellContext


class SpellEnvironment(Record):
    """Authored world facts only; never skill, Magery, HT or approval claims.

    This private adapter remains unavailable to player payloads until concrete
    effects and their world/combat channels have executable consumers.
    """

    target_id: Id
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"
    distance: int = Field(default=0, ge=0, le=10000)
    radius: int = Field(default=1, ge=1, le=100)
    energy: int = Field(default=1, ge=1, le=100)


def approved_context(
    play: PlayService, state: PlayState, command: SpellCommand, environment: SpellEnvironment
) -> SpellContext:
    compiler = play.engine.reviewer.compiler
    if compiler.statistics_profile != PROFILE:
        raise ValidationError("Spellcasting requires the exact Basic Set profile")
    spell_key = "spell:" + command.spell_id
    expected = {d.id: d for d in definitions()}
    if compiler.definitions.get(spell_key) != expected[spell_key]:
        raise ValidationError("Spell is not bound to the pinned learning catalog")
    actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
    if actor is None or actor.approval is None:
        raise ValidationError("Caster requires an approved build")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor.actor_id
    )
    purchases = {p.definition_id: p.amount for p in build.purchases}
    if spell_key not in purchases:
        raise ValidationError("Spell was not purchased and approved")
    values = {v.target: int(v.value) for v in build.sheet.values}
    learned = tuple(
        k.removeprefix("spell:") for k in purchases if k in expected and k.startswith("spell:")
    )
    target_ht = 10
    if SPELLS[command.spell_id].kind == "resisted":
        target = next((a for a in state.actors if a.actor_id == environment.target_id), None)
        if target is None or target.approval is None:
            raise ValidationError("Resisted spell requires an approved target build")
        target_build, _ = play.engine.reviewer.activate(
            target.proposal,
            target.approval,
            campaign_id=state.campaign_id,
            actor_id=target.actor_id,
        )
        target_ht = next(
            int(v.value) for v in target_build.sheet.values if v.target == "attribute:ht"
        )
    return SpellContext(
        profile_id=PROFILE,
        build_revision=build.revision,
        skill=values[spell_key],
        magery=magery_level(purchases),
        learned=learned,
        ht=values["attribute:ht"],
        will=values["secondary:will"],
        target_ht=target_ht,
        unavailable=bool(actor.conditions) or actor.available_at > state.resources.game_time,
        **environment.model_dump(),
    )
