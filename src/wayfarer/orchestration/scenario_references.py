"""Attach immutable scenario identities at activation and continuation boundaries."""

from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.campaign.scenario_document import ScenarioReference, digest_json
from wayfarer.engine.simulation.campaign.scenario_loading import PublishedRevision, ScenarioBoundary
from wayfarer.engine.simulation.campaign.scenario_references import boundary
from wayfarer.engine.simulation.campaign.studio import ScenarioGraph


def pin_scenario(
    campaign: Campaign,
    graph: ScenarioGraph,
    *,
    runtime_digest: str,
    command_id: str,
    campaign_revision: int,
    published: PublishedRevision | None = None,
    catalog_id: str | None = None,
) -> None:
    previous = boundary(campaign)
    graph_digest = digest_json(graph.model_dump(mode="json"))
    reference = ScenarioReference(
        catalog_id=catalog_id,
        revision=published.revision if published else 1,
        content_digest=published.report.content_digest if published else graph_digest,
        engine_digest=published.report.engine_digest if published else runtime_digest,
    )
    pin = ScenarioBoundary(
        reference=reference,
        graph_digest=graph_digest,
        runtime_digest=runtime_digest,
        command_id=command_id,
        campaign_revision=campaign_revision,
        segment=previous.segment + 1 if previous and previous.command_id != "setup:create" else 0,
        published=published,
    )
    campaign["scenario_graph_json"] = graph.model_dump_json()
    campaign["scenario_reference_json"] = pin.model_dump_json()
    if published is not None:
        campaign["scenario_document_json"] = published.content_json
    else:
        campaign.pop("scenario_document_json", None)
