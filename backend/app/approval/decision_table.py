"""Explicit Section-6 decision table for Break-Glass Voice Gate.

Table rules:
- PASS       + Valid authorised approval         -> PERMIT simulated action
- PASS       + Pending                           -> LOCKED
- PASS       + Rejected                          -> BLOCK
- BORDERLINE + Pending peer review               -> LOCKED
- BORDERLINE + Approved after required review    -> PERMIT simulated action
- FAIL       + Any approval attempt              -> BLOCK (never overridable)
- Any result + Expired approval                  -> REJECT approval
- Any result + Reused approval                   -> REJECT approval
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DecisionOutcome = Literal["PERMIT", "LOCKED", "BLOCK", "REJECT"]


@dataclass(frozen=True)
class DecisionResult:
    allowed: bool
    outcome: DecisionOutcome
    reason: str


def evaluate_decision(
    *,
    voice_risk: str | None,
    approval_status: str | None,
    approval_expired: bool = False,
    approval_reused: bool = False,
    peer_review_confirmed: bool = False,
) -> DecisionResult:
    """Evaluate the combined voice + second-device approval decision table."""
    # Hard voice failure can NEVER be overridden by second-device approval.
    if voice_risk == "FAIL":
        return DecisionResult(
            allowed=False,
            outcome="BLOCK",
            reason=(
                "Voice verification failed. A second-device approval cannot override a hard "
                "voice-verification failure; action remains blocked. Contact the employee via "
                "a previously verified channel."
            ),
        )

    if approval_reused or approval_status == "CONSUMED":
        return DecisionResult(
            allowed=False,
            outcome="REJECT",
            reason="Approval has already been used (one-time use enforced).",
        )

    if approval_expired or approval_status == "EXPIRED":
        return DecisionResult(
            allowed=False,
            outcome="REJECT",
            reason="Approval has expired after the 2-minute window. Generate a new approval record.",
        )

    if voice_risk is None:
        return DecisionResult(
            allowed=False,
            outcome="LOCKED",
            reason="Voice verification has not been completed yet; request remains locked.",
        )

    if approval_status == "REJECTED":
        return DecisionResult(
            allowed=False,
            outcome="BLOCK",
            reason="Second-device approver rejected this break-glass request; action is blocked.",
        )

    if approval_status in (None, "PENDING"):
        if voice_risk == "BORDERLINE":
            return DecisionResult(
                allowed=False,
                outcome="LOCKED",
                reason="Borderline voice result is locked pending peer review by a different authorised person.",
            )
        return DecisionResult(
            allowed=False,
            outcome="LOCKED",
            reason="Waiting for second-device number-matching approval; request remains locked.",
        )

    if approval_status == "APPROVED":
        if voice_risk == "PASS":
            return DecisionResult(
                allowed=True,
                outcome="PERMIT",
                reason="Voice check passed and authorised second-device approval is valid; simulated action permitted.",
            )
        if voice_risk == "BORDERLINE":
            if peer_review_confirmed:
                return DecisionResult(
                    allowed=True,
                    outcome="PERMIT",
                    reason="Borderline request approved after required different-person peer review; simulated action permitted.",
                )
            return DecisionResult(
                allowed=False,
                outcome="LOCKED",
                reason="Borderline request requires documented peer review confirmation before execution.",
            )

    return DecisionResult(
        allowed=False,
        outcome="BLOCK",
        reason="Unrecognized verification or approval state; failed closed.",
    )
