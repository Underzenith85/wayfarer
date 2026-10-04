"""B481 observer reports of critical item Power, separate from canonical truth."""

from wayfarer.engine.rules.magic.protocols import MagicItemInstance
from wayfarer.engine.simulation.magic.analyze_magic_state import AnalysisReport
from wayfarer.engine.simulation.magic.detect_magic_state import Detection
from wayfarer.engine.simulation.resources import Item, ResourceState

PREFIX = "enchantment-power:"


def reported_power(
    state: ResourceState, actor_ids: tuple[str, ...], binding: MagicItemInstance
) -> int | None:
    """Latest meaningful observer claim, across both source-valid discoveries."""
    for event in reversed(state.events):
        if event.id.startswith("analyze-magic:report:"):
            report = AnalysisReport.model_validate_json(event.kind)
            if (
                report.actor_id in actor_ids
                and (report.item_id, report.binding_id, report.project_id, report.actual_power)
                == (binding.item_id, binding.id, binding.project_id, binding.power)
                and report.claimed_power is not None
            ):
                return report.claimed_power
        elif event.id.startswith("detect-magic:finding:"):
            finding = Detection.model_validate_json(event.kind)
            if finding.actor_id in actor_ids and (
                finding.target_id,
                finding.binding_id,
                finding.project_id,
                finding.power,
            ) == (binding.item_id, binding.id, binding.project_id, binding.power):
                return finding.power
    return None


def item_projection(
    state: ResourceState, item: Item, actor_ids: tuple[str, ...] = ()
) -> dict[str, object]:
    unknown_projects = frozenset(
        event.target_id for event in state.events if event.id.startswith(PREFIX)
    )
    projected: list[dict[str, object]] = []
    for binding in item.enchantments:
        if binding.project_id not in unknown_projects:
            projected.append(binding.model_dump(mode="json"))
            continue
        value = binding.model_dump(mode="json", exclude={"power"})
        reported = reported_power(state, actor_ids, binding)
        if reported is not None:
            value["power"] = reported
        projected.append(value)
    result = item.model_dump(mode="json")
    if "enchantments" in result or projected:
        result["enchantments"] = projected
    return result
