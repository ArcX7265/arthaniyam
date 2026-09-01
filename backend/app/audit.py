from datetime import datetime, timezone
from hashlib import sha256
import json

from app.policy.models import StrictModel
from app.runtime.models import AuditEvent
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


class AuditEvidenceEntry(StrictModel):
    sequence: int
    previous_hash: str
    event_hash: str
    event: AuditEvent


class AuditEvidenceBundle(StrictModel):
    format_version: str
    exported_at: datetime
    policy_id: str
    policy_version: int
    event_count: int
    head_hash: str
    entries: list[AuditEvidenceEntry]
    bundle_hash: str


class PortableEvidenceVerification(StrictModel):
    valid: bool
    event_count: int
    calculated_head_hash: str
    calculated_bundle_hash: str
    issues: list[str]


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

    def export_bundle(self, policy_id: str, version: int) -> AuditEvidenceBundle:
        integrity = self.verify(policy_id, version)
        if not integrity.valid:
            raise ValueError("cannot export an audit chain that fails integrity checks")
        if not integrity.total_events or integrity.head_hash is None:
            raise ValueError("cannot export an empty audit chain")
        entries = [
            AuditEvidenceEntry(
                sequence=int(row["sequence"]),
                previous_hash=row["previous_hash"],
                event_hash=row["event_hash"],
                event=AuditEvent.model_validate_json(row["event_json"]),
            )
            for row in self.repository.audit_chain_rows(policy_id, version)
        ]
        core = self._bundle_core(
            policy_id,
            version,
            integrity.head_hash,
            entries,
        )
        return AuditEvidenceBundle(
            format_version="arthaniyam.audit.v1",
            exported_at=datetime.now(timezone.utc),
            policy_id=policy_id,
            policy_version=version,
            event_count=len(entries),
            head_hash=integrity.head_hash,
            entries=entries,
            bundle_hash=self._digest(core),
        )

    @classmethod
    def verify_bundle(
        cls, bundle: AuditEvidenceBundle
    ) -> PortableEvidenceVerification:
        issues: list[str] = []
        expected_previous = AUDIT_GENESIS_HASH
        for expected_sequence, entry in enumerate(bundle.entries, start=1):
            if entry.sequence != expected_sequence:
                issues.append(
                    f"expected sequence {expected_sequence}, found {entry.sequence}"
                )
            if entry.previous_hash != expected_previous:
                issues.append(f"sequence {entry.sequence} has a broken previous-hash link")
            calculated = audit_event_hash(
                expected_previous,
                entry.event.model_dump_json(),
            )
            if calculated != entry.event_hash:
                issues.append(
                    f"sequence {entry.sequence} event content hash does not match"
                )
            expected_previous = entry.event_hash
        if bundle.event_count != len(bundle.entries):
            issues.append("declared event count differs from bundle entry count")
        if bundle.head_hash != expected_previous:
            issues.append("declared head hash differs from calculated chain head")
        core = cls._bundle_core(
            bundle.policy_id,
            bundle.policy_version,
            bundle.head_hash,
            bundle.entries,
        )
        calculated_bundle_hash = cls._digest(core)
        if bundle.bundle_hash != calculated_bundle_hash:
            issues.append("bundle manifest hash does not match")
        return PortableEvidenceVerification(
            valid=not issues,
            event_count=len(bundle.entries),
            calculated_head_hash=expected_previous,
            calculated_bundle_hash=calculated_bundle_hash,
            issues=issues,
        )

    @staticmethod
    def _bundle_core(
        policy_id: str,
        version: int,
        head_hash: str,
        entries: list[AuditEvidenceEntry],
    ) -> dict:
        return {
            "format_version": "arthaniyam.audit.v1",
            "policy_id": policy_id,
            "policy_version": version,
            "event_count": len(entries),
            "head_hash": head_hash,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        }

    @staticmethod
    def _digest(value: dict) -> str:
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "sha256:" + sha256(canonical).hexdigest()
