from hashlib import sha256
import json

from app.runtime.models import FinancialAction


def approval_binding(
    policy_id: str, policy_version: int, action: FinancialAction
) -> str:
    canonical = json.dumps(
        {
            "policy_id": policy_id,
            "policy_version": policy_version,
            "action": action.model_dump(mode="json", exclude={"approval_ids"}),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(canonical).hexdigest()
