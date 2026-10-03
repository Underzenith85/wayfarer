"""Exercise explicit historical missile timing through the real command pipeline."""

from wayfarer.engine.simulation.magic.spell_state import SpellResult
from wayfarer.engine.simulation.magic.spells import SpellCommand
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spell_generations import recorded_generations
from wayfarer.orchestration.spells import SpellService


class _HistoricalMissileService(SpellService):
    async def execute(self, cid: str, value: object, *, principal_id: str) -> SpellResult:
        command = SpellCommand.model_validate(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        state = play._load(campaign)
        generations = await recorded_generations(play, state, command)
        return await submit(
            play,
            cid,
            self.plan(
                play,
                member_for(state, principal_id),
                command,
                principal_id=principal_id,
                capture_targeting=generations.capture_targeting,
                check_symptoms=generations.check_symptoms,
                item_sight=generations.item_sight,
                area_targeting=generations.area_targeting,
                missile_attack=False,
                state=state,
            ),
            principal_id=principal_id,
        )


def missile_spell_service(play: PlayService, *, missile_attack: bool) -> SpellService:
    """Fresh commands use the production service; old commands omit only the marker."""
    return SpellService(play) if missile_attack else _HistoricalMissileService(play)
