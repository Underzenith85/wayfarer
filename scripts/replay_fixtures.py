"""Reviewed replay fixtures and their deterministic engine bindings (development only)."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field

from wayfarer import validation
from wayfarer.engine.rules.randomness import RNG_ALGORITHM
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.events import EngineEvent, campaign_document, digest, document, fold
from wayfarer.models import Campaign, CommandReceipt, Record
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import CommandRecord, StoredEvent, payload_digest
from wayfarer.persistence.replay import ReplayCheck, require_configuration, verify_commands

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/replay"
CASES = (
    "reference",
    "capture-rescue",
    "hex-combat",
    "spell",
    "recovery",
    "fatigue-turn",
    "fatigue-defense",
)


class FixtureCommand(Record):
    command_id: str
    actor_id: str
    expected_revision: int
    resulting_revision: int
    action: str
    command_input: str
    seed: str
    recorded_at_us: int
    state_digest: str
    events: tuple[EngineEvent, ...]

    def record(self, before: Campaign, after: Campaign) -> CommandRecord:
        return CommandRecord(
            campaign_id=before["id"],
            command_id=self.command_id,
            actor_id=self.actor_id,
            expected_revision=self.expected_revision,
            resulting_revision=self.resulting_revision,
            payload_hash=payload_digest({"input": self.command_input}),
            rules_version=before["rules"],
            event=CommandReceipt(action=validation.event_action(self.action), outcome=""),
            state_after=after,
            entropy_seed=self.seed,
            rng_algorithm=RNG_ALGORITHM,
            recorded_at_us=self.recorded_at_us,
            command_input=self.command_input,
        )


class ReplayFixture(Record):
    schema_version: Literal[1] = 1
    name: str
    configuration_digest: str
    initial_json: str
    commands: tuple[FixtureCommand, ...] = Field(min_length=1)


async def engine_for(name: str, directory: Path) -> ActionEngine:
    """Bind the same reviewed rules as the acceptance fixtures; no model provider."""
    if name == "reference":
        from test_wave14 import Table

        from wayfarer.transport.campaign_api import ACCESS_KEY

        table = Table(directory / "reference.sqlite")
        await table.open()
        try:
            await table.start()
            assert table.client
            play = table.client.app[ACCESS_KEY].play
            return play.for_campaign(await play.store.read(table.cid)).engine
        finally:
            await table.close()
    if name in ("capture-rescue", "recovery"):
        from test_wave10 import prepare

        _, play = await prepare(directory)
        return play.engine
    if name == "hex-combat":
        from test_tactical import setup

        _, play = await setup(directory)
        return play.engine
    if name in ("fatigue-turn", "fatigue-defense"):
        from test_gurps_melee import setup as melee_setup

        _, play = await melee_setup(directory, "gurps-basic-set-4e-2004")
        return play.engine
    if name == "spell":
        from test_spell_bindings import setup as spell_setup

        _, play = await spell_setup(directory, execution_version=2)
        return play.engine
    raise ValueError(f"Unknown replay fixture {name}")


class FixtureExecutor:
    def __init__(self, engine: ActionEngine, directory: Path) -> None:
        self.engine, self.directory, self.count = engine, directory, 0

    async def __call__(
        self, before: Campaign, command: CommandRecord
    ) -> tuple[Campaign, list[EngineEvent]]:
        self.count += 1
        store = AsyncSQLiteStore(self.directory / f"command-{self.count}.sqlite")
        await store.insert(before)
        play = PlayService(store, self.engine).for_campaign(before)
        await execute_recorded(play, command)
        history = await store.history(before["id"])
        if len(history) != 1:
            raise ValueError("Re-execution must commit exactly one command")
        return await store.read(before["id"]), [
            e.event for e in await store.stream(before["id"], after=before["revision"])
        ]


async def verify_fixture(
    fixture: ReplayFixture, engine: ActionEngine, directory: Path
) -> tuple[ReplayCheck, ...]:
    initial = validation.campaign(validation.decode(fixture.initial_json))
    # Verify against the actual bound engine, not just the fixture's own pin.
    bound = PlayService(AsyncSQLiteStore(directory / "unused.sqlite"), engine).for_campaign(initial)
    bound._load(initial)
    before = initial
    records: list[CommandRecord] = []
    stream: list[StoredEvent] = []
    for command in fixture.commands:
        after = fold(before, list(command.events))
        if digest(document(after)) != command.state_digest:
            raise ValueError("Fixture snapshot digest diverges from event fold")
        record = command.record(before, after)
        records.append(record)
        stream.extend(
            StoredEvent(before["id"], command.command_id, command.resulting_revision, i, event)
            for i, event in enumerate(command.events)
        )
        before = after
    _, checks = await verify_commands(
        initial,
        records,
        stream,
        configuration_digest=fixture.configuration_digest,
        execute=FixtureExecutor(engine, directory),
    )
    if not all(c.folded and c.reexecuted for c in checks):
        raise ValueError(f"Fixture contains unverified commands: {checks}")
    return checks


async def regenerate(
    fixture: ReplayFixture, engine: ActionEngine, directory: Path
) -> ReplayFixture:
    """Keep original inputs and seeds; update only events and digests."""
    before = validation.campaign(validation.decode(fixture.initial_json))
    require_configuration(before, fixture.configuration_digest)
    executor = FixtureExecutor(engine, directory)
    commands: list[FixtureCommand] = []
    for command in fixture.commands:
        # The expected snapshot is not an input to execute_recorded.
        record = command.record(before, before)
        after, events = await executor(before, record)
        require_configuration(after, fixture.configuration_digest)
        commands.append(
            command.model_copy(
                update={"events": tuple(events), "state_digest": digest(document(after))}
            )
        )
        before = after
    return fixture.model_copy(update={"commands": tuple(commands)})


async def capture(name: str, directory: Path) -> ReplayFixture:
    """Initial reviewed cases; future regeneration keeps these initial states fixed."""
    from test_wave10 import choose, setback, wait

    from wayfarer.engine.simulation.actions import Inspect, Wait
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
    from wayfarer.orchestration.spells import SpellService

    table = None
    if name == "reference":
        from test_wave14 import Table

        from wayfarer.transport.campaign_api import ACCESS_KEY

        table = Table(directory / "reference.sqlite")
        await table.open()
        await table.start()
        assert table.client
        play = table.client.app[ACCESS_KEY].play.for_campaign(
            await table.client.app[ACCESS_KEY].play.store.read(table.cid)
        )
        cid = table.cid
    elif name in ("capture-rescue", "recovery"):
        from test_wave10 import prepare

        cid, play = await prepare(directory)
    elif name == "hex-combat":
        from test_tactical import setup

        cid, play = await setup(directory)
    elif name in ("fatigue-turn", "fatigue-defense"):
        from test_combat_settlement import condition
        from test_gurps_melee import attack
        from test_gurps_melee import setup as melee_setup

        cid, play = await melee_setup(directory, "gurps-basic-set-4e-2004")
        if name == "fatigue-defense":
            await attack(cid, play)
        # Reviewed genesis: fatigue collapse with an unaffected injury record.
        # The defense case retains an attack that still needs resolution.
        genesis = await play.store.read(cid)
        state = condition(play._load(genesis), "b", "collapsed")
        genesis["play_json"] = state.model_dump_json()
        store = AsyncSQLiteStore(directory / "fatigue-genesis.sqlite")
        await store.insert(genesis)
        play = PlayService(store, play.engine)
    elif name == "spell":
        from test_spell_bindings import setup as spell_setup

        cid, play = await spell_setup(directory, execution_version=2)
    else:
        raise ValueError(name)
    play.rng = secrets
    initial = await play.store.read(cid)
    baseline = len(await play.store.history(cid))
    try:
        if name == "reference":
            for index, target in enumerate(("manifest", "records")):
                state = play._load(await play.store.read(cid))
                result = await play.execute(
                    cid,
                    Inspect(
                        id=f"inspect-{index}",
                        actor_id="a",
                        expected_revision=state.revision,
                        target_id=target,
                    ),
                    authenticated_actor_id="a",
                )
                if result.status != "committed":
                    break
        elif name == "capture-rescue":
            await setback(cid, play, "capture")
            await choose(cid, play, "a", "observe")
            await wait(cid, play, "b")
            await choose(cid, play, "b", "rescue")
            await choose(cid, play, "a", "assist")
        elif name == "recovery":
            await setback(cid, play, "hurt")
            await choose(cid, play, "a", "rest")
            await wait(cid, play, "b", 2)
        elif name == "hex-combat":
            state = play._load(initial)
            await CombatService(play).execute(
                cid,
                TakeCombatTurn(
                    id="replay-attack",
                    actor_id="a",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    maneuver="attack",
                    item_id="sword-a",
                    mode_id="swing",
                    target_id="b",
                ),
                authenticated_actor_id="a",
            )
        elif name in ("fatigue-turn", "fatigue-defense"):
            from test_gurps_melee import choice

            state = play._load(initial)
            command = (
                choice()
                if name == "fatigue-defense"
                else TakeCombatTurn(
                    id="fatigue-rest",
                    actor_id="a",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    maneuver="do_nothing",
                )
            )
            await CombatService(play).execute(cid, command, authenticated_actor_id=command.actor_id)
            settled = play._load(await play.store.read(cid)).encounters[0]
            assert settled.status == "completed" and settled.completion_reason == "incapacitation"
        elif name == "spell":
            from test_spell_bindings import command as spell_command

            await SpellService(play).execute(cid, spell_command(0), principal_id="a")
            state = play._load(await play.store.read(cid))
            await play.execute(
                cid,
                Wait(id="spell-wait", actor_id="a", expected_revision=state.revision, ticks=1),
                authenticated_actor_id="a",
            )
        rows = (await play.store.history(cid))[baseline:]
        stream = await play.store.stream(cid, after=initial["revision"])
        commands = []
        for row in rows:
            assert row.entropy_seed and row.recorded_at_us is not None and row.command_input
            commands.append(
                FixtureCommand(
                    command_id=row.command_id,
                    actor_id=row.actor_id,
                    expected_revision=row.expected_revision,
                    resulting_revision=row.resulting_revision,
                    action=row.event["action"],
                    command_input=row.command_input,
                    seed=row.entropy_seed,
                    recorded_at_us=row.recorded_at_us,
                    state_digest=digest(document(row.state_after)),
                    events=tuple(e.event for e in stream if e.command_id == row.command_id),
                )
            )
        return ReplayFixture(
            name=name,
            configuration_digest=play._load(initial).configuration_digest,
            initial_json=json.dumps(campaign_document(document(initial))),
            commands=tuple(commands),
        )
    finally:
        if table is not None:
            await table.close()
