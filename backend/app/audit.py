from datetime import datetime, timezone

from app.policy.models import StrictModel
from app.runtime.storage import (
    AUDIT_GENESIS_HASH,
    SQLiteRuntimeRepository,
    audit_event_hash,
)


class AuditIntegrityReport(StrictModel):
    policy_id: str
    policy_version: int
    valid: bool
    total_events: int
    chained_events: int
    head_hash: str | None
    first_broken_sequence: int | None
    issues: list[str]
    verified_at: datetime


class AuditIntegrityService:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def verify(self, policy_id: str, version: int) -> AuditIntegrityReport:
        rows = self.repository.audit_chain_rows(policy_id, version)
        head = self.repository.audit_chain_head(policy_id, version)
        total_events = self.repository.audit_event_count(policy_id, version)
        issues: list[str] = []
        first_broken: int | None = None
        expected_previous = AUDIT_GENESIS_HASH

        for expected_sequence, row in enumerate(rows, start=1):
            sequence = int(row["sequence"])
            broken = False
            if sequence != expected_sequence:
                issues.append(
                    f"expected sequence {expected_sequence}, found {sequence}"
                )
                broken = True
            if row["previous_hash"] != expected_previous:
                issues.append(f"sequence {sequence} has a broken previous-hash link")
                broken = True
            if row["event_json"] is None:
                issues.append(f"sequence {sequence} references a missing audit event")
                broken = True
            else:
                calculated = audit_event_hash(expected_previous, row["event_json"])
                if calculated != row["event_hash"]:
                    issues.append(f"sequence {sequence} event content hash does not match")
                    broken = True
            if broken and first_broken is None:
                first_broken = sequence
            expected_previous = row["event_hash"]

        if total_events != len(rows):
            issues.append(
                f"audit event count {total_events} differs from chained count {len(rows)}"
            )
            first_broken = first_broken or len(rows) + 1
        if head is None:
            if total_events:
                issues.append("audit chain head is missing")
                first_broken = first_broken or 1
            head_hash = None
        else:
            head_count, head_hash = head
            if head_count != len(rows):
                issues.append(
                    f"checkpoint count {head_count} differs from chain count {len(rows)}"
                )
                first_broken = first_broken or min(head_count, len(rows)) + 1
            if head_hash != expected_previous:
                issues.append("checkpoint head hash differs from the calculated chain head")
                first_broken = first_broken or max(1, len(rows))

        return AuditIntegrityReport(
            policy_id=policy_id,
            policy_version=version,
            valid=not issues,
            total_events=total_events,
            chained_events=len(rows),
            head_hash=head_hash,
            first_broken_sequence=first_broken,
            issues=issues,
            verified_at=datetime.now(timezone.utc),
        )
