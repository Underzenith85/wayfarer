"""Persistent boundary receipts. BEGIN IMMEDIATE serializes competing API workers.

Use a separate SQLite file from the engine database: domain transactions never
wait for their own boundary lock. No provider call belongs in this transaction.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from wayfarer.orchestration.clock import CommandInstant, capture_instant

from .common import Obj, encoded, obj


class Transaction:
    def __init__(self, db: aiosqlite.Connection, instant: CommandInstant) -> None:
        self.db = db
        self.instant = instant

    async def get(self, key: str) -> Obj | None:
        async with self.db.execute("SELECT value FROM v1_records WHERE key=?", (key,)) as c:
            row = await c.fetchone()
        return obj(json.loads(row[0])) if row else None

    async def put(self, key: str, value: Obj) -> None:
        await self.db.execute(
            "INSERT INTO v1_records VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, encoded(value)),
        )

    async def items(self, prefix: str) -> list[Obj]:
        async with self.db.execute(
            "SELECT value FROM v1_records WHERE substr(key,1,?)=? ORDER BY key",
            (len(prefix), prefix),
        ) as c:
            rows = await c.fetchall()
        return [obj(json.loads(row[0])) for row in rows]


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    @asynccontextmanager
    async def transaction(
        self, *, instant: CommandInstant | None = None
    ) -> AsyncIterator[Transaction]:
        instant = instant if instant is not None else capture_instant()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path, timeout=30) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS v1_records (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            await db.commit()
            await db.execute("BEGIN IMMEDIATE")
            try:
                yield Transaction(db, instant)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
