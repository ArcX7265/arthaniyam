from __future__ import annotations

import sqlite3
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.policy.models import PolicyDefinition
from app.runtime.models import AuditEvent, FinancialAction, RuntimeComparison


DEFAULT_DATABASE_PATH = Path(
    os.getenv(
        "ARTHANIYAM_DATABASE_PATH",
        str(Path(__file__).resolve().parents[2] / "arthaniyam.sqlite3"),
    )
)


@dataclass
class StoredLedgerEntry:
    action: FinancialAction
    status: str
    created_at: datetime


@dataclass
class StoredEvaluation:
    fingerprint: str
    response: RuntimeComparison


class SQLiteRuntimeRepository:
    """Small durable repository with one connection per operation.

    SQLite keeps the prototype zero-setup while preserving policies, ledger
    entries, evaluation results, audit events, and provider executions across
    process restarts.
    """

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS policies (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    policy_json TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version)
                );

                CREATE TABLE IF NOT EXISTS ledger_entries (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version, action_id),
                    FOREIGN KEY (policy_id, version)
                        REFERENCES policies(policy_id, version)
                );

                CREATE TABLE IF NOT EXISTS evaluations (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version, action_id),
                    FOREIGN KEY (policy_id, version)
                        REFERENCES policies(policy_id, version)
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY (policy_id, version)
                        REFERENCES policies(policy_id, version)
                );

                CREATE TABLE IF NOT EXISTS provider_executions (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version, action_id),
                    FOREIGN KEY (policy_id, version, action_id)
                        REFERENCES ledger_entries(policy_id, version, action_id)
                );
                """
            )

    def reset(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM provider_executions")
            connection.execute("DELETE FROM audit_events")
            connection.execute("DELETE FROM evaluations")
            connection.execute("DELETE FROM ledger_entries")
            connection.execute("DELETE FROM policies")

    def register_policy(self, policy: PolicyDefinition) -> None:
        existing = self.get_policy(policy.policy_id, policy.version)
        if existing is not None and existing != policy:
            raise ValueError(
                "policy_id and version already refer to different policy contents"
            )
        if existing is None:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO policies(policy_id, version, policy_json) VALUES (?, ?, ?)",
                    (policy.policy_id, policy.version, policy.model_dump_json()),
                )

    def get_policy(self, policy_id: str, version: int) -> PolicyDefinition | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT policy_json FROM policies WHERE policy_id = ? AND version = ?",
                (policy_id, version),
            ).fetchone()
        return PolicyDefinition.model_validate_json(row["policy_json"]) if row else None

    def upsert_entry(
        self,
        policy_id: str,
        version: int,
        action: FinancialAction,
        status: str,
        created_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ledger_entries(
                    policy_id, version, action_id, action_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id, version, action_id) DO UPDATE SET
                    action_json = excluded.action_json,
                    status = excluded.status,
                    created_at = excluded.created_at
                """,
                (
                    policy_id,
                    version,
                    action.action_id,
                    action.model_dump_json(),
                    status,
                    created_at.isoformat(),
                ),
            )

    def get_entry(
        self, policy_id: str, version: int, action_id: str
    ) -> StoredLedgerEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT action_json, status, created_at FROM ledger_entries
                WHERE policy_id = ? AND version = ? AND action_id = ?
                """,
                (policy_id, version, action_id),
            ).fetchone()
        return self._entry_from_row(row) if row else None

    def list_entries(self, policy_id: str, version: int) -> list[StoredLedgerEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT action_json, status, created_at FROM ledger_entries
                WHERE policy_id = ? AND version = ? ORDER BY created_at
                """,
                (policy_id, version),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def save_evaluation(
        self,
        policy_id: str,
        version: int,
        action_id: str,
        fingerprint: str,
        response: RuntimeComparison,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluations(
                    policy_id, version, action_id, fingerprint, response_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(policy_id, version, action_id) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    response_json = excluded.response_json
                """,
                (
                    policy_id,
                    version,
                    action_id,
                    fingerprint,
                    response.model_dump_json(),
                ),
            )

    def get_evaluation(
        self, policy_id: str, version: int, action_id: str
    ) -> StoredEvaluation | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT fingerprint, response_json FROM evaluations
                WHERE policy_id = ? AND version = ? AND action_id = ?
                """,
                (policy_id, version, action_id),
            ).fetchone()
        if row is None:
            return None
        return StoredEvaluation(
            fingerprint=row["fingerprint"],
            response=RuntimeComparison.model_validate_json(row["response_json"]),
        )

    def append_audit(self, policy_id: str, version: int, event: AuditEvent) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(event_id, policy_id, version, event_json)
                VALUES (?, ?, ?, ?)
                """,
                (event.event_id, policy_id, version, event.model_dump_json()),
            )

    def list_audit(self, policy_id: str, version: int) -> list[AuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_json FROM audit_events
                WHERE policy_id = ? AND version = ? ORDER BY rowid
                """,
                (policy_id, version),
            ).fetchall()
        return [AuditEvent.model_validate_json(row["event_json"]) for row in rows]

    def get_execution(
        self, policy_id: str, version: int, action_id: str
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM provider_executions
                WHERE policy_id = ? AND version = ? AND action_id = ?
                """,
                (policy_id, version, action_id),
            ).fetchone()
        return row["result_json"] if row else None

    def save_execution(
        self,
        policy_id: str,
        version: int,
        action_id: str,
        result_json: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_executions(
                    policy_id, version, action_id, result_json
                ) VALUES (?, ?, ?, ?)
                """,
                (policy_id, version, action_id, result_json),
            )

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> StoredLedgerEntry:
        return StoredLedgerEntry(
            action=FinancialAction.model_validate_json(row["action_json"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
