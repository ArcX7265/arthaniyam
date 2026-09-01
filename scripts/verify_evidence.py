"""Verify an exported ArthaNiyam audit bundle using only Python's stdlib."""

from hashlib import sha256
import json
from pathlib import Path
import sys


GENESIS_HASH = "sha256:" + "0" * 64


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return "sha256:" + sha256(canonical(value).encode("utf-8")).hexdigest()


def verify(bundle: dict) -> list[str]:
    issues: list[str] = []
    expected_previous = GENESIS_HASH
    entries = bundle.get("entries", [])
    for expected_sequence, entry in enumerate(entries, start=1):
        sequence = entry.get("sequence")
        if sequence != expected_sequence:
            issues.append(f"expected sequence {expected_sequence}, found {sequence}")
        if entry.get("previous_hash") != expected_previous:
            issues.append(f"sequence {sequence} has a broken previous-hash link")
        event_hash = "sha256:" + sha256(
            f"{expected_previous}|{canonical(entry.get('event'))}".encode("utf-8")
        ).hexdigest()
        if entry.get("event_hash") != event_hash:
            issues.append(f"sequence {sequence} event content hash does not match")
        expected_previous = entry.get("event_hash")
    if bundle.get("event_count") != len(entries):
        issues.append("declared event count differs from bundle entry count")
    if bundle.get("head_hash") != expected_previous:
        issues.append("declared head hash differs from calculated chain head")
    core = {
        "format_version": bundle.get("format_version"),
        "policy_id": bundle.get("policy_id"),
        "policy_version": bundle.get("policy_version"),
        "event_count": len(entries),
        "head_hash": bundle.get("head_hash"),
        "entries": entries,
    }
    if bundle.get("bundle_hash") != digest(core):
        issues.append("bundle manifest hash does not match")
    return issues


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/verify_evidence.py <bundle.json|->")
        return 2
    if sys.argv[1] == "-":
        bundle = json.load(sys.stdin)
    else:
        path = Path(sys.argv[1])
        bundle = json.loads(path.read_text(encoding="utf-8"))
    issues = verify(bundle)
    if issues:
        print("INVALID ArthaNiyam evidence bundle")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(
        f"VALID ArthaNiyam evidence bundle: {bundle['event_count']} events, "
        f"head {bundle['head_hash']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
