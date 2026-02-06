"""
LifeOS Assistant — Chore Database.

The Memory pillar: chores persist in SQLite across days, surviving bot restarts.
Chores are added exclusively via the /addchore Telegram command.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from src.data.models import Chore

logger = logging.getLogger(__name__)


class ChoreDB:
    """SQLite-backed storage for recurring chores."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            from src.config import settings
            db_path = settings.DATABASE_PATH

        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create the chores table if it doesn't exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chores (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT    NOT NULL,
                    frequency_days  INTEGER NOT NULL,
                    last_done       TEXT,
                    next_due        TEXT    NOT NULL,
                    assigned_to     TEXT    NOT NULL,
                    active          INTEGER NOT NULL DEFAULT 1
                )
            """)
        logger.debug("Chores table initialized at %s", self._db_path)

    @staticmethod
    def _row_to_chore(row: sqlite3.Row) -> Chore:
        return Chore(
            id=row["id"],
            name=row["name"],
            frequency_days=row["frequency_days"],
            last_done=row["last_done"],
            next_due=row["next_due"],
            assigned_to=row["assigned_to"],
            active=bool(row["active"]),
        )

    def add_chore(
        self,
        name: str,
        frequency_days: int,
        assigned_to: str,
        start_date: str | None = None,
    ) -> Chore:
        """Insert a new chore. next_due defaults to start_date (or today)."""
        if start_date is None:
            start_date = date.today().isoformat()

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chores (name, frequency_days, last_done, next_due, assigned_to, active)
                VALUES (?, ?, NULL, ?, ?, 1)
                """,
                (name, frequency_days, start_date, assigned_to),
            )
            chore_id = cursor.lastrowid

        chore = Chore(
            id=chore_id,
            name=name,
            frequency_days=frequency_days,
            last_done=None,
            next_due=start_date,
            assigned_to=assigned_to,
            active=True,
        )
        logger.info("Chore added: #%d '%s' every %d days", chore_id, name, frequency_days)
        return chore

    def get_due_chores(self, target_date: str | None = None) -> list[Chore]:
        """Return all active chores where next_due <= target_date."""
        if target_date is None:
            target_date = date.today().isoformat()

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chores WHERE active = 1 AND next_due <= ? ORDER BY next_due",
                (target_date,),
            ).fetchall()

        return [self._row_to_chore(r) for r in rows]

    def mark_done(self, chore_id: int) -> Chore:
        """Mark a chore as done today. Calculates next next_due."""
        today = date.today()

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM chores WHERE id = ?", (chore_id,)).fetchone()
            if row is None:
                raise ValueError(f"Chore {chore_id} not found")

            freq = row["frequency_days"]
            next_due = (today + timedelta(days=freq)).isoformat()

            conn.execute(
                "UPDATE chores SET last_done = ?, next_due = ? WHERE id = ?",
                (today.isoformat(), next_due, chore_id),
            )

        chore = self._row_to_chore(row)
        chore.last_done = today.isoformat()
        chore.next_due = next_due
        logger.info("Chore #%d '%s' marked done, next due: %s", chore_id, chore.name, next_due)
        return chore

    def list_all(self, active_only: bool = True) -> list[Chore]:
        """List all chores, optionally filtered to active only."""
        query = "SELECT * FROM chores"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY next_due"

        with self._connect() as conn:
            rows = conn.execute(query).fetchall()

        return [self._row_to_chore(r) for r in rows]

    def delete_chore(self, chore_id: int) -> bool:
        """Soft-delete a chore (set active = False)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE chores SET active = 0 WHERE id = ? AND active = 1",
                (chore_id,),
            )
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Chore #%d soft-deleted", chore_id)
        return deleted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    db = ChoreDB(db_path="data/test_chores.db")
    chore = db.add_chore("Take out trash", frequency_days=7, assigned_to="Amit")
    print(f"Added: {chore}")

    chore = db.add_chore("Clean kitchen", frequency_days=3, assigned_to="Dana")
    print(f"Added: {chore}")

    print(f"\nAll chores: {db.list_all()}")
    print(f"Due today: {db.get_due_chores()}")

    done = db.mark_done(1)
    print(f"\nMarked done: {done}")

    db.delete_chore(2)
    print(f"After delete: {db.list_all()}")
