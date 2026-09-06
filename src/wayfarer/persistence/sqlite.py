"""SQLite adapter retaining the original demo schema and transactional guarantees."""
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from wayfarer.models import Campaign, Event


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, state TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS events (campaign TEXT, request_id TEXT, payload TEXT, PRIMARY KEY(campaign, request_id))')
                yield db
        finally:
            db.close()

    def insert(self, state: Campaign) -> None:
        with self.connection() as db:
            db.execute('INSERT INTO campaigns VALUES (?, ?)', (state['id'], json.dumps(state)))

    @staticmethod
    def _read(db: sqlite3.Connection, cid: str) -> Campaign:
        row = db.execute('SELECT state FROM campaigns WHERE id=?', (cid,)).fetchone()
        if row is None:
            raise ValueError('Campaign not found')
        return cast(Campaign, json.loads(row[0]))

    def read(self, cid: str) -> Campaign:
        with self.connection() as db:
            return self._read(db, cid)

    def listing(self) -> list[dict[str, str]]:
        with self.connection() as db:
            rows = db.execute('SELECT state FROM campaigns ORDER BY rowid DESC').fetchall()
        states = [cast(Campaign, json.loads(row[0])) for row in rows]
        return [{'id': s['id'], 'title': s['scenario']['title'], 'name': s['character']['name']} for s in states]

    @staticmethod
    def _duplicate(db: sqlite3.Connection, cid: str, request_id: str, text: str) -> bool:
        row = db.execute('SELECT payload FROM events WHERE campaign=? AND request_id=?', (cid, request_id)).fetchone()
        if row is None:
            return False
        if json.loads(row[0])['input'] != text:
            raise ValueError('Request ID already used for different input')
        return True

    def duplicate(self, cid: str, request_id: str, text: str) -> bool:
        with self.connection() as db:
            return self._duplicate(db, cid, request_id, text)

    def commit_turn(self, cid: str, request_id: str, revision: int, text: str,
                    resolve: Callable[[Campaign], Event]) -> tuple[Campaign, Event | None]:
        """Resolve only under the revision lock; callback must contain no external I/O."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._read(db, cid)
            if self._duplicate(db, cid, request_id, text):
                return state, None
            if state['revision'] != revision:
                raise ValueError('Campaign changed. Refresh before retrying.')
            event = resolve(state)
            db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(state), cid))
            db.execute('INSERT INTO events VALUES (?,?,?)', (cid, request_id, json.dumps(event)))
            return state, event

    def save_narration(self, cid: str, revision: int, narration: str) -> None:
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._read(db, cid)
            if state['revision'] == revision:
                state['messages'][-1]['flavor'] = narration
                db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(state), cid))
