"""Create the B236 encounter as part of the authorized backfire transaction."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.reinforcements import admit_actor
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import StartEncounter
from wayfarer.engine.simulation.combat.encounter import CombatAllegiance, SideOpposition
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield
from wayfarer.engine.simulation.magic.backfire_transitions import (
    BackfireSelection,
    ResolveSpellBackfire,
)
from wayfarer.orchestration.combat.context import CombatContext
from wayfarer.orchestration.combat.encounters import _start_encounter
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat
from wayfarer.orchestration.play import PlayService


def start_summon(
    play: PlayService, state: PlayState, command: ResolveSpellBackfire, selection: BackfireSelection
) -> PlayState:
    declaration = command.summon_encounter
    assert declaration is not None and selection.choice.position is not None
    engine = play.engine.combat
    assert engine is not None
    board = engine.battlefields[declaration.battlefield_id]
    x, y = selection.choice.position
    placement = Placement(
        actor_id=selection.target_id,
        position=Hex(q=x, r=y) if isinstance(board, HexBattlefield) else GridPoint(x=x, y=y),
        facing=declaration.summoned_facing,
        hex_facing=declaration.summoned_hex_facing,
    )
    # The table specifies hostile intent. Explicit opposition preserves an
    # encounter involving a helpless victim until the GM actually ends it.
    start = StartEncounter(
        id=command.id + ":summon",
        actor_id=command.actor_id,
        expected_revision=state.revision,
        encounter_id=declaration.encounter_id,
        battlefield_id=declaration.battlefield_id,
        scene_id=declaration.scene_id,
        placements=(declaration.caster, placement),
        allegiances=(
            CombatAllegiance(actor_id=selection.item.actor_id, side_id="caster"),
            CombatAllegiance(actor_id=selection.target_id, side_id="summoned"),
        ),
        oppositions=(SideOpposition(side_ids=("caster", "summoned")),),
    )
    if state.party.groups:
        state = admit_actor(state, selection.target_id, selection.item.actor_id)
    context = CombatContext(play, state)
    # This is an involuntary appearance, not a voluntary action by the caster.
    # The same canonical constructor retains approval, map, scene, hand and
    # resource validation. Settlement reads the victim's real incapacity.
    step = _start_encounter(
        state, start, context, involuntary_actor_ids=frozenset({selection.item.actor_id})
    )
    step, encounters = _settle_combat(step, start, state.encounters + (step.encounter,), context)
    updated, _ = _finish_combat(step, start, encounters, context)
    return updated
