from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from amao.models import Milestone, MilestoneStatus, ProgressSummary


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
            self._add_column_if_missing(conn, "milestones", "started_at", "TIMESTAMP")
            self._add_column_if_missing(conn, "milestones", "completed_at", "TIMESTAMP")

    @staticmethod
    def _add_column_if_missing(
        conn: sqlite3.Connection, table: str, column: str, coltype: str
    ) -> None:
        # SQLite has no "ADD COLUMN IF NOT EXISTS" -- this keeps repeated
        # StateManager.__init__ calls against an existing db file idempotent.
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise

    def create_milestones(self, milestones: list[dict[str, str]]) -> None:
        # Delegates to add_milestone per row, which trades the previous
        # single-transaction-for-the-whole-batch behavior for one transaction
        # per row -- an acceptable minor behavior change to avoid duplicating
        # the INSERT.
        for m in milestones:
            self.add_milestone(m["title"], m["description"])

    def add_milestone(self, title: str, description: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO milestones (title, description, status) VALUES (?, ?, ?)",
                (title, description, MilestoneStatus.PENDING.value),
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
                    """
                    UPDATE milestones
                    SET status = ?,
                        attempts = ?,
                        last_error = ?,
                        started_at = COALESCE(
                            started_at,
                            CASE WHEN ? = 'IN_PROGRESS' THEN CURRENT_TIMESTAMP END
                        ),
                        completed_at = CASE
                            WHEN ? = 'COMPLETED' THEN CURRENT_TIMESTAMP
                            ELSE completed_at
                        END
                    WHERE id = ?
                    """,
                    (
                        status.value,
                        attempts,
                        last_error,
                        status.value,
                        status.value,
                        milestone_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE milestones
                    SET status = ?,
                        last_error = ?,
                        started_at = COALESCE(
                            started_at,
                            CASE WHEN ? = 'IN_PROGRESS' THEN CURRENT_TIMESTAMP END
                        ),
                        completed_at = CASE
                            WHEN ? = 'COMPLETED' THEN CURRENT_TIMESTAMP
                            ELSE completed_at
                        END
                    WHERE id = ?
                    """,
                    (status.value, last_error, status.value, status.value, milestone_id),
                )

    def log(self, milestone_id: int, step: str, details: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_logs (milestone_id, step, details) VALUES (?, ?, ?)",
                (milestone_id, step, json.dumps(details)),
            )

    def get_audit_logs(
        self, milestone_id: int | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            if milestone_id is not None:
                cursor = conn.execute(
                    "SELECT * FROM audit_logs WHERE milestone_id = ? ORDER BY id DESC LIMIT ?",
                    (milestone_id, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            rows = cursor.fetchall()

        logs = []
        for row in rows:
            try:
                details: Any = json.loads(row["details"])
            except (TypeError, ValueError):
                details = row["details"]
            logs.append(
                {
                    "id": row["id"],
                    "milestone_id": row["milestone_id"],
                    "step": row["step"],
                    "details": details,
                    "timestamp": row["timestamp"],
                }
            )
        return logs

    def get_progress_summary(self) -> ProgressSummary:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT status, title, attempts, started_at, completed_at "
                "FROM milestones ORDER BY id ASC"
            ).fetchall()

        total = len(rows)
        pending = sum(1 for r in rows if r["status"] == MilestoneStatus.PENDING.value)
        in_progress = sum(1 for r in rows if r["status"] == MilestoneStatus.IN_PROGRESS.value)
        completed = sum(1 for r in rows if r["status"] == MilestoneStatus.COMPLETED.value)
        halted = sum(1 for r in rows if r["status"] == MilestoneStatus.HALTED.value)

        current_milestone_title: str | None = None
        current_milestone_attempts = 0
        for r in rows:
            if r["status"] == MilestoneStatus.IN_PROGRESS.value:
                current_milestone_title = r["title"]
                current_milestone_attempts = r["attempts"]
                break

        durations = []
        for r in rows:
            if r["started_at"] is not None and r["completed_at"] is not None:
                started = datetime.strptime(r["started_at"], "%Y-%m-%d %H:%M:%S")
                finished = datetime.strptime(r["completed_at"], "%Y-%m-%d %H:%M:%S")
                durations.append((finished - started).total_seconds())

        average_completed_seconds = sum(durations) / len(durations) if durations else None
        estimated_remaining_seconds = (
            average_completed_seconds * (pending + in_progress)
            if average_completed_seconds is not None
            else None
        )

        return ProgressSummary(
            total=total,
            pending=pending,
            in_progress=in_progress,
            completed=completed,
            halted=halted,
            current_milestone_title=current_milestone_title,
            current_milestone_attempts=current_milestone_attempts,
            average_completed_seconds=average_completed_seconds,
            estimated_remaining_seconds=estimated_remaining_seconds,
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
