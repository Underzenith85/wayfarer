"""Process-local campaign serialization and immutable compiled configuration reuse."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from weakref import WeakKeyDictionary, WeakValueDictionary

from wayfarer.character.power import PowerReviewer
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.simulation.action_engine import ActionEngine, _configuration_digest
from wayfarer.simulation.actions import ActionRules
from wayfarer.simulation.resources import ResourceEngine

Store = AsyncSQLiteStore | AsyncPostgresStore
EngineKey = tuple[str, frozenset[str], frozenset[str]]


@dataclass
class Session:
    lock: asyncio.Lock
    touched: float
    engine: ActionEngine | None = None
    users: int = 0


class SessionRegistry:
    def __init__(
        self, *, idle_seconds: float = 900, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.idle_seconds, self.clock = idle_seconds, clock
        self.sessions: WeakKeyDictionary[Store, dict[str, Session]] = WeakKeyDictionary()
        self.engines: WeakValueDictionary[EngineKey, ActionEngine] = WeakValueDictionary()

    def evict_idle(self) -> int:
        now = self.clock()
        removed = 0
        for campaigns in list(self.sessions.values()):
            for cid, session in list(campaigns.items()):
                if (
                    session.users == 0
                    and not session.lock.locked()
                    and now - session.touched >= self.idle_seconds
                ):
                    del campaigns[cid]
                    removed += 1
        return removed

    def session(self, store: Store, cid: str) -> Session:
        self.evict_idle()
        campaigns = self.sessions.setdefault(store, {})
        session = campaigns.setdefault(cid, Session(asyncio.Lock(), self.clock()))
        session.touched = self.clock()
        return session

    def bind(
        self,
        store: Store,
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
            engine = ActionEngine(reviewer, resources, rules)
            self.engines[key] = engine
        self.session(store, cid).engine = engine
        return engine

    @asynccontextmanager
    async def serialized(self, store: Store, cid: str) -> AsyncIterator[Session]:
        session = self.session(store, cid)
        session.users += 1
        try:
            async with session.lock:
                yield session
        finally:
            session.users -= 1
            session.touched = self.clock()


REGISTRY = SessionRegistry()
