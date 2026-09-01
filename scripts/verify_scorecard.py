"""Verify an exported ArthaNiyam judge scorecard using only Python's stdlib."""

from hashlib import sha256
import json
from pathlib import Path
import sys


def digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()


def verify(bundle: dict) -> list[str]:
    issues: list[str] = []
    if bundle.get("format_version") != "arthaniyam.judge.v1":
        issues.append("unsupported format version")
    scorecard = bundle.get("scorecard", {})
    checks = scorecard.get("checks", [])
    evidence_core = {
        "request": {
            "seed": scorecard.get("campaign_seed"),
            "samples_per_class": scorecard.get("samples_per_class"),
        },
        "proof_evidence_hash": scorecard.get("proof_evidence_hash"),
        "benchmark_evidence_hash": scorecard.get("benchmark_evidence_hash"),
        "campaign_evidence_hash": scorecard.get("campaign_evidence_hash"),
        "checks": checks,
    }
    if scorecard.get("evidence_hash") != digest(evidence_core):
        issues.append("scorecard evidence hash does not match its evaluation inputs")
    bundle_core = {
        "format_version": bundle.get("format_version"),
        "scorecard": scorecard,
    }
    if bundle.get("bundle_hash") != digest(bundle_core):
        issues.append("bundle manifest hash does not match")
    passed = sum(check.get("passed") is True for check in checks)
    if scorecard.get("checks_passed") != passed:
        issues.append("declared passed-check count differs from the checks")
    if scorecard.get("total_checks") != len(checks):
        issues.append("declared total-check count differs from the checks")
    verdict = "ready" if passed == len(checks) else "needs_attention"
    if scorecard.get("verdict") != verdict:
        issues.append("verdict is inconsistent with the check results")
    if scorecard.get("total_test_cases") != (
        scorecard.get("fixed_scenarios", 0) + scorecard.get("generated_cases", 0)
    ):
        issues.append("declared test-case total differs from its components")
    return issues


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/verify_scorecard.py <scorecard.json|->")
        return 2
    bundle = (
        json.load(sys.stdin)
        if sys.argv[1] == "-"
        else json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    )
    issues = verify(bundle)
    if issues:
        print("INVALID ArthaNiyam judge scorecard")
        for issue in issues:
            print(f"- {issue}")
        return 1
    scorecard = bundle["scorecard"]
    print(
        f"VALID ArthaNiyam judge scorecard: {scorecard['checks_passed']}/"
        f"{scorecard['total_checks']} checks, {scorecard['total_test_cases']} cases, "
        f"evidence {scorecard['evidence_hash']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
