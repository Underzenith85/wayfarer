"""Per-campaign serialization and immutable compiled configuration reuse.

A registry belongs to one runtime. Nothing here is a module global, so two
runtimes over two stores never share a lock, an engine cache or a clock.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from weakref import WeakValueDictionary

from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.simulation.action_engine.digest import _configuration_digest
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore

Store = AsyncSQLiteStore | AsyncPostgresStore
EngineKey = tuple[str, frozenset[str], frozenset[str]]
EngineFactory = Callable[[PowerReviewer, ResourceEngine, ActionRules], ActionEngine]


@dataclass
class Session:
    lock: asyncio.Lock
    touched: float
    engine: ActionEngine | None = None
    users: int = 0


class SessionRegistry:
    def __init__(
        self,
        *,
        idle_seconds: float = 900,
        clock: Callable[[], float] = time.monotonic,
        engine_factory: EngineFactory = ActionEngine,
    ) -> None:
        self.idle_seconds, self.clock = idle_seconds, clock
        self.engine_factory = engine_factory
        self.sessions: dict[str, Session] = {}
        self.engines: WeakValueDictionary[EngineKey, ActionEngine] = WeakValueDictionary()

    def evict_idle(self) -> int:
        now = self.clock()
        removed = 0
        for cid, session in list(self.sessions.items()):
            if (
                session.users == 0
                and not session.lock.locked()
                and now - session.touched >= self.idle_seconds
            ):
                del self.sessions[cid]
                removed += 1
        return removed

    def session(self, cid: str) -> Session:
        self.evict_idle()
        session = self.sessions.setdefault(cid, Session(asyncio.Lock(), self.clock()))
        session.touched = self.clock()
        return session

    def bind(
        self,
        cid: str,
        reviewer: PowerReviewer,
        resources: ResourceEngine,
        rules: ActionRules,
    ) -> ActionEngine:
        digest = _configuration_digest(reviewer, resources, rules)
        # These existing immutable bindings are not included in the legacy digest.
        # Never share authorization or actor catalogs across different bindings.
        key = (digest, resources.actors, reviewer.gm_ids)
        engine = self.engines.get(key)
        if engine is None:
            engine = self.engine_factory(reviewer, resources, rules)
            self.engines[key] = engine
        self.session(cid).engine = engine
        return engine

    @asynccontextmanager
    async def serialized(self, cid: str) -> AsyncIterator[Session]:
        session = self.session(cid)
        session.users += 1
        try:
            async with session.lock:
                yield session
        finally:
            session.users -= 1
            session.touched = self.clock()
