from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from amao.models import Milestone, MilestoneStatus


class StateManager:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, commit/rollback the transaction, and always close it.

        `with sqlite3.connect(...) as conn:` only manages the transaction --
        it does not close the connection -- so every call using that pattern
        leaks a connection/file handle. This helper closes it in `finally`.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS milestones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT UNIQUE,
                    description TEXT,
                    status TEXT,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_milestones_status_id ON milestones(status, id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    milestone_id INTEGER,
                    step TEXT,
                    details TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def create_milestones(self, milestones: list[dict[str, str]]) -> None:
        with self._connect() as conn:
            for m in milestones:
                conn.execute(
                    "INSERT OR IGNORE INTO milestones (title, description, status) "
                    "VALUES (?, ?, ?)",
                    (m["title"], m["description"], MilestoneStatus.PENDING.value),
                )

    def get_next_pending_milestone(self) -> Milestone | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM milestones WHERE status IN (?, ?, ?) ORDER BY id ASC LIMIT 1",
                (
                    MilestoneStatus.PENDING.value,
                    MilestoneStatus.IN_PROGRESS.value,
                    MilestoneStatus.HALTED.value,
                ),
            )
            row = cursor.fetchone()
            return self._row_to_milestone(row) if row else None

    def count_milestones(self) -> int:
        with self._connect() as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM milestones").fetchone()
            return int(count)

    def update_milestone_status(
        self,
        milestone_id: int,
        status: MilestoneStatus,
        attempts: int | None = None,
        last_error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            if attempts is not None:
                conn.execute(
                    "UPDATE milestones SET status = ?, attempts = ?, last_error = ? WHERE id = ?",
                    (status.value, attempts, last_error, milestone_id),
                )
            else:
                conn.execute(
                    "UPDATE milestones SET status = ?, last_error = ? WHERE id = ?",
                    (status.value, last_error, milestone_id),
                )

    def log(self, milestone_id: int, step: str, details: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_logs (milestone_id, step, details) VALUES (?, ?, ?)",
                (milestone_id, step, json.dumps(details)),
            )

    @staticmethod
    def _row_to_milestone(row: sqlite3.Row) -> Milestone:
        return Milestone(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            status=MilestoneStatus(row["status"]),
            attempts=row["attempts"],
            last_error=row["last_error"],
        )
