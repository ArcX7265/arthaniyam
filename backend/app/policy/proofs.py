from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal

from app.policy.models import StrictModel
from app.policy.verifier import (
    VerificationRequest,
    VerificationResult,
    verify_correlated_payments,
)
from app.runtime.storage import SQLiteRuntimeRepository, StoredProof


class ProofRecord(StrictModel):
    proof_run_id: str
    evidence_hash: str
    created_at: datetime
    request: VerificationRequest
    result: VerificationResult


class ProofReplayResult(StrictModel):
    proof_run_id: str
    status: Literal["verified", "mismatch"]
    replayed_at: datetime
    stored_integrity_verified: bool
    replay_matches_original: bool
    original_evidence_hash: str
    replayed_evidence_hash: str
    result: VerificationResult


def evidence_hash(
    request: VerificationRequest, result: VerificationResult
) -> str:
    canonical = json.dumps(
        {
            "request": request.model_dump(mode="json"),
            "result": result.model_dump(mode="json", exclude={"evidence_hash"}),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(canonical).hexdigest()


class ProofService:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def record(
        self, request: VerificationRequest, result: VerificationResult
    ) -> VerificationResult:
        digest = evidence_hash(request, result)
        enriched = result.model_copy(update={"evidence_hash": digest})
        self.repository.save_proof(
            result.proof_run_id,
            request.model_dump_json(),
            enriched.model_dump_json(),
            digest,
            datetime.now(timezone.utc),
        )
        return enriched

    def get(self, proof_run_id: str) -> ProofRecord:
        stored = self.repository.get_proof(proof_run_id)
        if stored is None:
            raise KeyError(proof_run_id)
        return self._record(proof_run_id, stored)

    def list(self, limit: int) -> list[ProofRecord]:
        return [self._record(run_id, stored) for run_id, stored in self.repository.list_proofs(limit)]

    def replay(self, proof_run_id: str) -> ProofReplayResult:
        original = self.get(proof_run_id)
        stored_digest = evidence_hash(original.request, original.result)
        replayed = verify_correlated_payments(original.request)
        replayed_digest = evidence_hash(original.request, replayed)
        stored_ok = stored_digest == original.evidence_hash
        replay_ok = replayed_digest == original.evidence_hash
        return ProofReplayResult(
            proof_run_id=proof_run_id,
            status="verified" if stored_ok and replay_ok else "mismatch",
            replayed_at=datetime.now(timezone.utc),
            stored_integrity_verified=stored_ok,
            replay_matches_original=replay_ok,
            original_evidence_hash=original.evidence_hash,
            replayed_evidence_hash=replayed_digest,
            result=replayed.model_copy(update={"evidence_hash": replayed_digest}),
        )

    @staticmethod
    def _record(proof_run_id: str, stored: StoredProof) -> ProofRecord:
        return ProofRecord(
            proof_run_id=proof_run_id,
            evidence_hash=stored.evidence_hash,
            created_at=stored.created_at,
            request=VerificationRequest.model_validate_json(stored.request_json),
            result=VerificationResult.model_validate_json(stored.result_json),
        )
