"""Async SQLite adapter with atomic command handling."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import aiosqlite

from wayfarer import validation
from wayfarer.errors import ConflictError, NotFoundError, StorageError
from wayfarer.models import Campaign, Event, TurnResult


class AsyncSQLiteStore:
    def __init__(self, path: Path, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout

    async def _connect(self) -> aiosqlite.Connection:
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path, timeout=self.timeout)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            "CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS events (campaign TEXT, request_id TEXT, payload TEXT, PRIMARY KEY(campaign, request_id))"
        )
        await db.commit()
        return db

    @staticmethod
    async def _read(db: aiosqlite.Connection, cid: str) -> Campaign:
        cursor = await db.execute("SELECT state FROM campaigns WHERE id=?", (cid,))
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise NotFoundError("Campaign not found")
        return validation.campaign(validation.decode(row[0]))

    async def insert(self, state: Campaign) -> None:
        db = await self._connect()
        try:
            await db.execute(
                "INSERT INTO campaigns VALUES (?, ?)", (state["id"], json.dumps(state))
            )
            await db.commit()
        except aiosqlite.Error as exc:
            await db.rollback()
            raise StorageError("Unable to save campaign") from exc
        finally:
            await db.close()

    async def read(self, cid: str) -> Campaign:
        db = await self._connect()
        try:
            return await self._read(db, cid)
        finally:
            await db.close()

    async def listing(self) -> list[dict[str, str]]:
        db = await self._connect()
        try:
            cursor = await db.execute("SELECT state FROM campaigns ORDER BY rowid DESC")
            rows = await cursor.fetchall()
            await cursor.close()
            states = [validation.campaign(validation.decode(row[0])) for row in rows]
            return [
                {"id": s["id"], "title": s["scenario"]["title"], "name": s["character"]["name"]}
                for s in states
            ]
        finally:
            await db.close()

    @staticmethod
    async def _duplicate(db: aiosqlite.Connection, cid: str, request_id: str, text: str) -> bool:
        cursor = await db.execute(
            "SELECT payload FROM events WHERE campaign=? AND request_id=?", (cid, request_id)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return False
        if validation.mapping(validation.decode(row[0])).get("input") != text:
            raise ConflictError("Request ID already used for different input")
        return True

    async def duplicate(self, cid: str, request_id: str, text: str) -> bool:
        db = await self._connect()
        try:
            return await self._duplicate(db, cid, request_id, text)
        finally:
            await db.close()

    async def commit_turn(
        self,
        cid: str,
        request_id: str,
        revision: int,
        text: str,
        resolve: Callable[[Campaign], Event],
    ) -> TurnResult:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            state = await self._read(db, cid)
            if await self._duplicate(db, cid, request_id, text):
                await db.rollback()
                return {"kind": "replayed", "state": state}
            if state["revision"] != revision:
                raise ConflictError("Campaign changed. Refresh before retrying.")
            event = resolve(state)
            await db.execute("UPDATE campaigns SET state=? WHERE id=?", (json.dumps(state), cid))
            await db.execute(
                "INSERT INTO events VALUES (?,?,?)", (cid, request_id, json.dumps(event))
            )
            await db.commit()
            return {"kind": "committed", "state": state, "event": event}
        except (ConflictError, NotFoundError):
            await db.rollback()
            raise
        except aiosqlite.Error as exc:
            await db.rollback()
            raise StorageError("Unable to commit turn") from exc
        finally:
            await db.close()

    async def save_narration(self, cid: str, revision: int, narration: str) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            state = await self._read(db, cid)
            if state["revision"] == revision:
                state["messages"][-1]["flavor"] = narration
                await db.execute(
                    "UPDATE campaigns SET state=? WHERE id=?", (json.dumps(state), cid)
                )
            await db.commit()
        finally:
            await db.close()
