from __future__ import annotations

from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field
from z3 import Int, Optimize, Sum, sat

from app.policy.models import PolicyDefinition, StrictModel


class VerificationRequest(StrictModel):
    policy: PolicyDefinition
    max_actions: int = Field(default=4, ge=2, le=10)


class CounterexampleAction(StrictModel):
    sequence: int
    amount: int
    vendor_id: str
    purpose: str
    invoice_id: str
    naive_gateway_decision: Literal["allow"] = "allow"
    reason: str


class Counterexample(StrictModel):
    attack: Literal["correlated_split_payment"] = "correlated_split_payment"
    actions: list[CounterexampleAction]
    correlated_total: int
    approval_threshold: int
    arthaniyam_decision: Literal["require_approval"] = "require_approval"
    violated_invariant: str
    explanation: str


class VerificationResult(StrictModel):
    policy_id: str
    policy_version: int
    status: Literal["counterexample_found", "no_counterexample_within_bound"]
    checked_bound: int
    proof_run_id: str
    counterexample: Counterexample | None = None
    limitation: str


def verify_correlated_payments(request: VerificationRequest) -> VerificationResult:
    """Search for the smallest split-payment sequence that bypasses approval.

    This is deliberately a bounded model check. It proves the presence of a
    counterexample when one is found; absence only applies to the tested bound.
    """

    policy = request.policy
    threshold = policy.approval.required_above
    individual_ceiling = min(policy.budget.per_transaction_limit, threshold)
    proof_run_id = str(
        uuid5(
            NAMESPACE_URL,
            (
                f"arthaniyam:{policy.model_dump_json()}:"
                f"max_actions={request.max_actions}"
            ),
        )
    )

    for action_count in range(2, request.max_actions + 1):
        optimizer = Optimize()
        amounts = [Int(f"amount_{index}") for index in range(action_count)]

        for amount in amounts:
            optimizer.add(amount > 0)
            optimizer.add(amount <= individual_ceiling)

        # Symmetry breaking gives stable, human-readable counterexamples.
        for index in range(action_count - 1):
            optimizer.add(amounts[index] <= amounts[index + 1])

        total = Sum(amounts)
        optimizer.add(total > threshold)
        optimizer.add(total <= policy.budget.monthly_limit)
        optimizer.minimize(total)
        optimizer.minimize(amounts[-1] - amounts[0])

        if optimizer.check() == sat:
            model = optimizer.model()
            concrete_amounts = [model.eval(amount).as_long() for amount in amounts]
            correlated_total = sum(concrete_amounts)
            actions = [
                CounterexampleAction(
                    sequence=index + 1,
                    amount=amount,
                    vendor_id="vendor-demo-001",
                    purpose="office-laptops",
                    invoice_id="invoice-demo-001",
                    reason=(
                        f"INR {amount / 100:,.2f} does not individually exceed "
                        f"the INR {threshold / 100:,.2f} approval threshold"
                    ),
                )
                for index, amount in enumerate(concrete_amounts)
            ]
            return VerificationResult(
                policy_id=policy.policy_id,
                policy_version=policy.version,
                status="counterexample_found",
                checked_bound=request.max_actions,
                proof_run_id=proof_run_id,
                counterexample=Counterexample(
                    actions=actions,
                    correlated_total=correlated_total,
                    approval_threshold=threshold,
                    violated_invariant=(
                        "Correlated commitments above the threshold require valid approval."
                    ),
                    explanation=(
                        f"A request-by-request gateway allows all {action_count} payments, "
                        f"but their shared commitment is INR {correlated_total / 100:,.2f}; "
                        "ArthaNiyam groups them before making the authorization decision."
                    ),
                ),
                limitation=(
                    f"Counterexample search was bounded to {request.max_actions} actions."
                ),
            )

    return VerificationResult(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        status="no_counterexample_within_bound",
        checked_bound=request.max_actions,
        proof_run_id=proof_run_id,
        limitation=(
            f"No counterexample found within the tested model of "
            f"{request.max_actions} actions. This is not an unbounded proof."
        ),
    )
