"""Normal application composition and bundled, provider-independent adventures."""

from pathlib import Path

from aiohttp import web

from wayfarer.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.config import Settings
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.profiles import ProfileRuntime
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.profiles import DEFAULT_REGISTRY, PROTOTYPE_PROFILE, RegisteredProfile
from wayfarer.simulation.actions import ActionEngine, ActionRules, ActorSetup
from wayfarer.simulation.objectives import Objective, ObjectiveRules, Predicate
from wayfarer.simulation.party import PartyRules
from wayfarer.simulation.resources import Owner, ResourceEngine, ResourceState
from wayfarer.simulation.scenes import Scene, SceneExit, SceneRules
from wayfarer.simulation.studio import GenerationBrief, ScenarioGraph
from wayfarer.transport.campaign_api import create_campaign_app
from wayfarer.world import Connection, Entity, EntityKind, World


def starting_scenario(players: int = 1) -> ScenarioGraph:
    """A short authored delivery adventure with a legal editable starting party."""
    actors = tuple(
        ActorSetup(
            actor_id=name.lower(),
            proposal=CharacterProposal(
                draft=CharacterDraft(
                    name=name,
                    purchases=tuple(
                        Purchase(definition_id=f"attribute:{key}", amount=10)
                        for key in ("st", "dx", "iq", "ht")
                    ),
                )
            ),
            aware_of=("harbor", "beacon"),
        )
        for name in ("Mira", "Iven")[:players]
    )
    return ScenarioGraph(
        id=f"beacon-{players}",
        version=1,
        title="The Last Beacon" + (" (two players)" if players == 2 else " (solo)"),
        brief=GenerationBrief(
            premise="Carry the harbor warning to the beacon before the storm arrives.",
            genre="Fantasy",
            tone="Adventurous",
            duration_minutes=30,
            difficulty="gentle",
        ),
        opening_scene_id="harbor-scene",
        opening_action="Explore the harbor, then travel to the beacon before the storm arrives.",
        failure_consequence="The storm arrives before the warning reaches the beacon.",
        world=World(
            entities=(
                Entity("harbor", EntityKind.LOCATION, "Stormbound Harbor"),
                Entity("beacon", EntityKind.LOCATION, "The Beacon"),
            )
            + tuple(
                Entity(a.actor_id, EntityKind.ACTOR, a.proposal.draft.name, location_id="harbor")
                for a in actors
            ),
            connections=(
                Connection("harbor", "beacon", "coastal path"),
                Connection("beacon", "harbor", "return path"),
            ),
        ),
        resources=ResourceState(
            owners=tuple(Owner(actor_id=a.actor_id, capacity=100) for a in actors)
        ),
        actors=actors,
        actions=ActionRules(id="beacon-actions", version=1),
        scenes=SceneRules(
            id="beacon-scenes",
            version=1,
            scenes=(
                Scene(
                    id="harbor-scene",
                    version=1,
                    location_id="harbor",
                    title="Stormbound Harbor",
                    exits=(SceneExit(id="to-beacon", destination_id="beacon-scene", ticks=2),),
                ),
                Scene(
                    id="beacon-scene",
                    version=1,
                    location_id="beacon",
                    title="The Beacon",
                    exits=(SceneExit(id="to-harbor", destination_id="harbor-scene", ticks=2),),
                ),
            ),
        ),
        objectives=ObjectiveRules(
            id="beacon-objectives",
            version=1,
            deadline=30,
            objectives=(
                Objective(
                    id="deliver-warning",
                    title="Reach the beacon before the storm",
                    predicates=tuple(
                        Predicate(kind="location", subject_id=a.actor_id, value="beacon")
                        for a in actors
                    ),
                ),
            ),
        ),
        party=PartyRules(id="beacon-party", version=1),
    )


def runtime_engine(profile: RegisteredProfile = PROTOTYPE_PROFILE) -> ActionEngine:
    """Build the play engine for one exact registered profile; pins never float."""
    catalog = profile.catalog
    return ActionEngine(
        PowerReviewer(
            CharacterCompiler(catalog, profile.rules, profile.policy),
            PowerPolicy(id="starter-power", version=1),
            frozenset(),
        ),
        ResourceEngine(starting_scenario().world, catalog, profile.rules, profile.policy, ()),
        ActionRules(id="starter-actions", version=1),
    )


def create_runtime_app(settings: Settings, frontend_dir: Path) -> web.Application:
    if not (frontend_dir / "index.html").is_file() or not (frontend_dir / "assets").is_dir():
        raise ValueError(
            "Frontend build missing. Run pnpm --dir frontend build, or set WAYFARER_FRONTEND_DIR."
        )
    if not settings.tokens:
        raise ValueError(
            "Set WAYFARER_TOKENS to a JSON object mapping access tokens to player names."
        )
    store = (
        AsyncPostgresStore(settings.database_url.get_secret_value(), settings.db_timeout_seconds)
        if settings.database_url
        else AsyncSQLiteStore(settings.db, settings.db_timeout_seconds)
    )
    runtime = ProfileRuntime(DEFAULT_REGISTRY, store, runtime_engine, PROTOTYPE_PROFILE)
    return create_campaign_app(
        CampaignAccess(runtime.play),
        {token: principal for token, principal in settings.tokens.items()},
        settings=settings if settings.llm_provider == "codex" or settings.llm_enabled else None,
        frontend_dir=frontend_dir,
        scenario_templates=(starting_scenario(), starting_scenario(2)),
        v1_ledger_path=settings.db.with_suffix(".v1.sqlite3"),
        v1_origins=frozenset(settings.allowed_origins),
    )
