"""Exit after projection/event writes but before command-log insertion commits."""

import asyncio
import os
import sys
from pathlib import Path

import aiosqlite

from wayfarer.models import Campaign, CommandReceipt
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


class CrashStore(AsyncSQLiteStore):
    async def _connect(self) -> aiosqlite.Connection:
        db = await super()._connect()
        await db.create_function("release_crash", 0, lambda: os._exit(73))
        await db.execute(
            "CREATE TEMP TRIGGER crash BEFORE INSERT ON command_log "
            "BEGIN SELECT release_crash(); END"
        )
        return db


def resolve(state: Campaign) -> CommandReceipt:
    state["revision"] += 1
    state["hp"] = 1
    return CommandReceipt(action="legacy", outcome="fault")


async def main() -> None:
    await CrashStore(Path(sys.argv[1])).commit_turn(sys.argv[2], "fault", 0, "fault", resolve)
    raise RuntimeError("Crash injection was not reached")


if __name__ == "__main__":
    asyncio.run(main())
