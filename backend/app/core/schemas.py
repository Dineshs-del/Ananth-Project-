"""Pydantic schemas for the Break-Glass Voice Gate HTTP API."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

EmployeeRole = Literal["requester", "approver", "security_admin"]
RequestStatus = Literal[
    "PENDING_VOICE",
    "PENDING_APPROVAL",
    "ESCALATED_REVIEW",
    "HIGH_RISK_HOLD",
    "APPROVED",
    "REJECTED",
    "EXPIRED",
]
RiskLevel = Literal["PASS", "BORDERLINE", "FAIL"]
DecisionAction = Literal["approve", "reject"]


class EmployeeCreate(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=120)
    role: EmployeeRole = "requester"
    known_callback_hint: str = Field(default="", max_length=160)

    @field_validator("employee_id")
    @classmethod
    def check_employee_id(cls, value: str) -> str:
        if not EMPLOYEE_ID_PATTERN.match(value):
            raise ValueError("employee_id must match ^[A-Za-z0-9_.:-]{1,64}$")
        return value


class EmployeeOut(BaseModel):
    employee_id: str
    display_name: str
    role: EmployeeRole
    known_callback_hint: str
    enrolled_samples: int
    created_at: str


class BreakGlassRequestCreate(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=64)
    action_summary: str = Field(..., min_length=3, max_length=200)
    target_resource: str = Field(..., min_length=2, max_length=160)
    justification: str = Field(..., min_length=5, max_length=1000)

    @field_validator("employee_id")
    @classmethod
    def check_employee_id(cls, value: str) -> str:
        if not EMPLOYEE_ID_PATTERN.match(value):
            raise ValueError("employee_id must match ^[A-Za-z0-9_.:-]{1,64}$")
        return value


class VoiceCheckResultOut(BaseModel):
    """Exact 4-key contract agreed with Member B and Member C."""

    aletheia: dict[str, Any] | None
    eidos: dict[str, Any] | None
    risk: RiskLevel
    reason: str


class BreakGlassRequestOut(BaseModel):
    request_id: str
    employee_id: str
    employee_name: str | None = None
    known_callback_hint: str = ""
    action_summary: str
    target_resource: str
    justification: str
    status: RequestStatus
    challenge_phrase: str
    challenge_expires_at: str
    voice_attempts: int
    voice_result: VoiceCheckResultOut | None
    reviewer_id: str | None
    decision_notes: str | None
    callback_verified: bool
    created_at: str
    updated_at: str
    decided_at: str | None


class DecisionRequest(BaseModel):
    reviewer_id: str = Field(..., min_length=1, max_length=64)
    action: DecisionAction
    notes: str = Field(default="", max_length=1000)
    callback_verified: bool = False

    @field_validator("reviewer_id")
    @classmethod
    def check_reviewer_id(cls, value: str) -> str:
        if not EMPLOYEE_ID_PATTERN.match(value):
            raise ValueError("reviewer_id must match ^[A-Za-z0-9_.:-]{1,64}$")
        return value


class AuditEventOut(BaseModel):
    id: int
    event_type: str
    actor_id: str
    request_id: str | None
    details: dict[str, Any]
    created_at: str
    prev_hash: str
    event_hash: str


class AuditChainVerificationOut(BaseModel):
    valid: bool
    checked_events: int
    broken_at_id: int | None
