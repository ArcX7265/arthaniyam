from __future__ import annotations

import sqlite3
import os
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.policy.models import PolicyDefinition
from app.runtime.models import (
    AuditEvent,
    DelegationComparison,
    DelegationGrant,
    FinancialAction,
    RuntimeComparison,
)


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


@dataclass
class StoredProof:
    request_json: str
    result_json: str
    evidence_hash: str
    created_at: datetime


@dataclass
class StoredDelegation:
    fingerprint: str
    grant: DelegationGrant
    response: DelegationComparison
    created_at: datetime


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
                    provider_order_id TEXT,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version, action_id),
                    FOREIGN KEY (policy_id, version, action_id)
                        REFERENCES ledger_entries(policy_id, version, action_id)
                );

                CREATE TABLE IF NOT EXISTS payment_confirmations (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version, action_id)
                );

                CREATE TABLE IF NOT EXISTS webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proof_runs (
                    proof_run_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approval_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    challenge_json TEXT NOT NULL,
                    UNIQUE(policy_id, version, action_id)
                );

                CREATE TABLE IF NOT EXISTS approval_grants (
                    approval_id TEXT PRIMARY KEY,
                    challenge_id TEXT NOT NULL,
                    binding_hash TEXT NOT NULL,
                    approver_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(challenge_id, approver_id),
                    FOREIGN KEY (challenge_id) REFERENCES approval_challenges(challenge_id)
                );

                CREATE TABLE IF NOT EXISTS authority_grants (
                    policy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    grant_id TEXT NOT NULL,
                    parent_agent_id TEXT NOT NULL,
                    child_agent_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    grant_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version, grant_id),
                    FOREIGN KEY (policy_id, version)
                        REFERENCES policies(policy_id, version)
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(provider_executions)"
                ).fetchall()
            }
            if "provider_order_id" not in columns:
                connection.execute(
                    "ALTER TABLE provider_executions ADD COLUMN provider_order_id TEXT"
                )

    def reset(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM authority_grants")
            connection.execute("DELETE FROM approval_grants")
            connection.execute("DELETE FROM approval_challenges")
            connection.execute("DELETE FROM proof_runs")
            connection.execute("DELETE FROM webhook_events")
            connection.execute("DELETE FROM payment_confirmations")
            connection.execute("DELETE FROM provider_executions")
            connection.execute("DELETE FROM audit_events")
            connection.execute("DELETE FROM evaluations")
            connection.execute("DELETE FROM ledger_entries")
            connection.execute("DELETE FROM policies")

    def save_delegation(
        self,
        policy_id: str,
        version: int,
        grant: DelegationGrant,
        fingerprint: str,
        response: DelegationComparison,
        created_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO authority_grants(
                    policy_id, version, grant_id, parent_agent_id, child_agent_id,
                    fingerprint, grant_json, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    policy_id,
                    version,
                    grant.grant_id,
                    grant.parent_agent_id,
                    grant.child_agent_id,
                    fingerprint,
                    grant.model_dump_json(),
                    response.model_dump_json(),
                    created_at.isoformat(),
                ),
            )

    def get_delegation(
        self, policy_id: str, version: int, grant_id: str
    ) -> StoredDelegation | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT fingerprint, grant_json, response_json, created_at
                FROM authority_grants
                WHERE policy_id = ? AND version = ? AND grant_id = ?""",
                (policy_id, version, grant_id),
            ).fetchone()
        return self._delegation_from_row(row) if row else None

    def list_delegations(
        self, policy_id: str, version: int
    ) -> list[StoredDelegation]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT fingerprint, grant_json, response_json, created_at
                FROM authority_grants WHERE policy_id = ? AND version = ?
                ORDER BY created_at""",
                (policy_id, version),
            ).fetchall()
        return [self._delegation_from_row(row) for row in rows]

    def save_approval_challenge(self, challenge_json: str) -> None:
        challenge = json.loads(challenge_json)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO approval_challenges(
                    challenge_id, policy_id, version, action_id, challenge_json
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    challenge["challenge_id"],
                    challenge["policy_id"],
                    challenge["policy_version"],
                    challenge["action_id"],
                    challenge_json,
                ),
            )

    def update_approval_challenge(self, challenge_json: str) -> None:
        challenge = json.loads(challenge_json)
        with self._connect() as connection:
            connection.execute(
                "UPDATE approval_challenges SET challenge_json = ? WHERE challenge_id = ?",
                (challenge_json, challenge["challenge_id"]),
            )

    def get_approval_challenge(self, challenge_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT challenge_json FROM approval_challenges WHERE challenge_id = ?",
                (challenge_id,),
            ).fetchone()
        return row["challenge_json"] if row else None

    def find_approval_challenge(
        self, policy_id: str, version: int, action_id: str
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT challenge_json FROM approval_challenges
                WHERE policy_id = ? AND version = ? AND action_id = ?""",
                (policy_id, version, action_id),
            ).fetchone()
        return row["challenge_json"] if row else None

    def save_approval_grant(
        self,
        approval_id: str,
        challenge_id: str,
        binding_hash: str,
        approver_id: str,
        expires_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO approval_grants(
                    approval_id, challenge_id, binding_hash, approver_id, status, expires_at
                ) VALUES (?, ?, ?, ?, 'active', ?)""",
                (
                    approval_id,
                    challenge_id,
                    binding_hash,
                    approver_id,
                    expires_at.isoformat(),
                ),
            )

    def valid_approval_count(
        self, approval_ids: list[str], binding_hash: str, now: datetime
    ) -> int:
        if not approval_ids:
            return 0
        placeholders = ",".join("?" for _ in approval_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT DISTINCT approver_id FROM approval_grants
                WHERE approval_id IN ({placeholders}) AND binding_hash = ?
                AND status = 'active' AND expires_at > ?""",
                (*approval_ids, binding_hash, now.isoformat()),
            ).fetchall()
        return len(rows)

    def consume_approval_grants(self, approval_ids: list[str]) -> None:
        if not approval_ids:
            return
        placeholders = ",".join("?" for _ in approval_ids)
        with self._connect() as connection:
            challenges = connection.execute(
                f"""SELECT DISTINCT challenge_id FROM approval_grants
                WHERE approval_id IN ({placeholders})""",
                approval_ids,
            ).fetchall()
            connection.execute(
                f"UPDATE approval_grants SET status = 'consumed' WHERE approval_id IN ({placeholders})",
                approval_ids,
            )
            for row in challenges:
                stored = connection.execute(
                    "SELECT challenge_json FROM approval_challenges WHERE challenge_id = ?",
                    (row["challenge_id"],),
                ).fetchone()
                if stored:
                    challenge = json.loads(stored["challenge_json"])
                    challenge["status"] = "consumed"
                    connection.execute(
                        """UPDATE approval_challenges SET challenge_json = ?
                        WHERE challenge_id = ?""",
                        (
                            json.dumps(challenge, separators=(",", ":")),
                            row["challenge_id"],
                        ),
                    )

    def save_proof(
        self,
        proof_run_id: str,
        request_json: str,
        result_json: str,
        evidence_hash: str,
        created_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO proof_runs(
                    proof_run_id, request_json, result_json, evidence_hash, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(proof_run_id) DO NOTHING""",
                (
                    proof_run_id,
                    request_json,
                    result_json,
                    evidence_hash,
                    created_at.isoformat(),
                ),
            )

    def get_proof(self, proof_run_id: str) -> StoredProof | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT request_json, result_json, evidence_hash, created_at
                FROM proof_runs WHERE proof_run_id = ?""",
                (proof_run_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredProof(
            request_json=row["request_json"],
            result_json=row["result_json"],
            evidence_hash=row["evidence_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_proofs(self, limit: int) -> list[tuple[str, StoredProof]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT proof_run_id, request_json, result_json,
                evidence_hash, created_at FROM proof_runs
                ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            (
                row["proof_run_id"],
                StoredProof(
                    request_json=row["request_json"],
                    result_json=row["result_json"],
                    evidence_hash=row["evidence_hash"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
            )
            for row in rows
        ]

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
        provider_order_id: str,
        result_json: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_executions(
                    policy_id, version, action_id, provider_order_id, result_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (policy_id, version, action_id, provider_order_id, result_json),
            )

    def find_execution_by_order(self, order_id: str) -> tuple[str, int, str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT policy_id, version, action_id, result_json
                FROM provider_executions WHERE provider_order_id = ?
                """,
                (order_id,),
            ).fetchone()
        if row is None:
            return None
        return row["policy_id"], row["version"], row["action_id"], row["result_json"]

    def get_confirmation(self, policy_id: str, version: int, action_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT result_json FROM payment_confirmations
                WHERE policy_id = ? AND version = ? AND action_id = ?""",
                (policy_id, version, action_id),
            ).fetchone()
        return row["result_json"] if row else None

    def save_confirmation(
        self, policy_id: str, version: int, action_id: str, result_json: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO payment_confirmations(
                policy_id, version, action_id, result_json
                ) VALUES (?, ?, ?, ?)""",
                (policy_id, version, action_id, result_json),
            )

    def has_webhook(self, event_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM webhook_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return row is not None

    def save_webhook(
        self, event_id: str, event_type: str, payload_hash: str, processed_at: datetime
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO webhook_events(
                event_id, event_type, payload_hash, processed_at
                ) VALUES (?, ?, ?, ?)""",
                (event_id, event_type, payload_hash, processed_at.isoformat()),
            )

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> StoredLedgerEntry:
        return StoredLedgerEntry(
            action=FinancialAction.model_validate_json(row["action_json"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _delegation_from_row(row: sqlite3.Row) -> StoredDelegation:
        return StoredDelegation(
            fingerprint=row["fingerprint"],
            grant=DelegationGrant.model_validate_json(row["grant_json"]),
            response=DelegationComparison.model_validate_json(row["response_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
